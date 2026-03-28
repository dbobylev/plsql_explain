using System;
using System.Collections.Generic;
using Antlr4.Runtime;
using Antlr4.Runtime.Misc;
using Antlr4.Runtime.Tree;
using PlsqlParser.Grammar;
using PlsqlParser.Model;

namespace PlsqlParser.Parser;

/// <summary>
/// Visits a PL/SQL parse tree (grammars-v4 PlSql grammar) and extracts
/// call edges, table access entries, subprogram definitions, and substatement trees.
///
/// Subprogram tracking:
///   - procedure_body / function_body  → subprograms inside a package body
///   - create_procedure_body / create_function_body → standalone top-level objects
///     (caller_subprogram stays null for standalone objects)
///
/// Call extraction:
///   - call_statement  → explicit CALL pkg.proc(...)
///   - general_element → inline function call expr like pkg.func(args)
///
/// Table access:
///   - DML statement visitors push the operation onto _dmlStack
///   - dml_table_expression_clause visitor records table if tableview_name present
///   - merge_statement handled directly (target is a plain tableview_name)
///
/// Substatement extraction:
///   - For each subprogram body extracts a tree of logical blocks:
///     IF/LOOP/CASE/BEGIN_END/SQL/EXCEPTION_HANDLER/DECLARE
/// </summary>
public class PlsqlVisitor : PlSqlParserBaseVisitor<object?>
{
    private static readonly HashSet<string> BuiltinPackages = new(StringComparer.OrdinalIgnoreCase)
    {
        "DBMS_OUTPUT", "DBMS_SQL", "DBMS_UTILITY", "DBMS_METADATA", "DBMS_LOCK",
        "DBMS_SESSION", "DBMS_APPLICATION_INFO", "DBMS_TRANSACTION",
        "UTL_FILE", "UTL_HTTP", "UTL_SMTP", "UTL_RAW", "UTL_I18N",
        "SYS", "STANDARD", "DUAL",
        "TO_DATE", "TO_CHAR", "TO_NUMBER", "TO_TIMESTAMP", "TO_CLOB",
        "NVL", "NVL2", "NULLIF", "COALESCE", "DECODE",
        "TRIM", "LTRIM", "RTRIM", "SUBSTR", "INSTR", "LENGTH",
        "UPPER", "LOWER", "REPLACE", "REGEXP_REPLACE", "REGEXP_LIKE",
        "SYSDATE", "SYSTIMESTAMP", "TRUNC", "ROUND", "FLOOR", "CEIL",
        "ABS", "MOD", "POWER", "SQRT",
        "RAISE_APPLICATION_ERROR", "ROWNUM", "ROWID",
    };

    private readonly string _callerSchema;
    private readonly string _callerObject;
    private readonly string _callerType;
    private readonly string _sourceText;

    // Stack of enclosing subprogram names (null = package level or standalone object)
    private readonly Stack<string?> _subprogramStack = new();
    private string? CurrentSubprogram => _subprogramStack.Count > 0 ? _subprogramStack.Peek() : null;

    // Stack of current DML operation for table access tracking
    private readonly Stack<string> _dmlStack = new();
    private string? CurrentDml => _dmlStack.Count > 0 ? _dmlStack.Peek() : null;

    public List<CallEdge> CallEdges { get; } = new();
    public List<TableAccess> TableAccesses { get; } = new();
    public List<SubprogramInfo> Subprograms { get; } = new();
    public List<SubstatementInfo> Substatements { get; } = new();

    private readonly HashSet<string> _edgeKeys = new();
    private readonly HashSet<string> _accessKeys = new();

    // Per-subprogram seq counter: key = subprogram name (null → "")
    private readonly Dictionary<string, int> _seqCounters = new();

    public PlsqlVisitor(string callerSchema, string callerObject, string callerType, string sourceText)
    {
        _callerSchema = callerSchema;
        _callerObject = callerObject;
        _callerType = callerType;
        _sourceText = sourceText;
        _subprogramStack.Push(null);
    }

    // ── Source text extraction ───────────────────────────────────────────────

    private string GetSourceText(ParserRuleContext ctx)
    {
        if (ctx.Stop == null) return ctx.GetText();
        int start = ctx.Start.StartIndex;
        int end = ctx.Stop.StopIndex;
        if (start < 0 || end < start || end >= _sourceText.Length) return ctx.GetText();
        return _sourceText.Substring(start, end - start + 1);
    }

    private int NextSeq(string? subprogram)
    {
        var key = subprogram ?? "";
        if (!_seqCounters.TryGetValue(key, out var current))
            current = 0;
        _seqCounters[key] = current + 1;
        return current;
    }

    // ── Subprogram boundary tracking ────────────────────────────────────────

    // Called for subprograms INSIDE a package body
    public override object? VisitProcedure_body([NotNull] PlSqlParser.Procedure_bodyContext ctx)
    {
        var name = ctx.identifier()?.GetText()?.ToUpperInvariant();
        _subprogramStack.Push(name);

        RecordSubprogram(ctx, name, "PROCEDURE");
        var result = base.VisitProcedure_body(ctx);
        ExtractSubprogramContent(ctx, ctx.IS(), ctx.AS(), ctx.seq_of_declare_specs(), ctx.body(), name);

        _subprogramStack.Pop();
        return result;
    }

    public override object? VisitFunction_body([NotNull] PlSqlParser.Function_bodyContext ctx)
    {
        var name = ctx.identifier()?.GetText()?.ToUpperInvariant();
        _subprogramStack.Push(name);

        RecordSubprogram(ctx, name, "FUNCTION");
        var result = base.VisitFunction_body(ctx);
        ExtractSubprogramContent(ctx, ctx.IS(), ctx.AS(), ctx.seq_of_declare_specs(), ctx.body(), name);

        _subprogramStack.Pop();
        return result;
    }

    // For top-level CREATE PROCEDURE / CREATE FUNCTION, caller_subprogram stays null —
    // no push needed; the root null on the stack is used.
    public override object? VisitCreate_procedure_body([NotNull] PlSqlParser.Create_procedure_bodyContext ctx)
    {
        RecordSubprogram(ctx, _callerObject, "PROCEDURE");
        var result = base.VisitCreate_procedure_body(ctx);
        ExtractSubprogramContent(ctx, ctx.IS(), ctx.AS(), ctx.seq_of_declare_specs(), ctx.body(), null);
        return result;
    }

    public override object? VisitCreate_function_body([NotNull] PlSqlParser.Create_function_bodyContext ctx)
    {
        RecordSubprogram(ctx, _callerObject, "FUNCTION");
        var result = base.VisitCreate_function_body(ctx);
        ExtractSubprogramContent(ctx, ctx.IS(), ctx.AS(), ctx.seq_of_declare_specs(), ctx.body(), null);
        return result;
    }

    private void RecordSubprogram(ParserRuleContext ctx, string? name, string type)
    {
        if (name == null) return;
        Subprograms.Add(new SubprogramInfo
        {
            Name = name,
            SubprogramType = type,
            StartLine = ctx.Start.Line,
            EndLine = ctx.Stop?.Line ?? ctx.Start.Line,
            SourceText = GetSourceText(ctx),
        });
    }

    // ── Substatement extraction ──────────────────────────────────────────────

    /// <summary>
    /// Extracts substatements for a subprogram's declare section + body.
    /// All are top-level children (parentSeq = null) with sequential positions.
    /// </summary>
    private void ExtractSubprogramContent(
        ParserRuleContext ownerCtx,
        ITerminalNode? isToken,
        ITerminalNode? asToken,
        PlSqlParser.Seq_of_declare_specsContext? declareSpecs,
        PlSqlParser.BodyContext? body,
        string? subprogram)
    {
        int pos = 0;
        int bodyStartIndex = body?.Start.StartIndex ?? -1;
        int headerEndIndex = isToken?.Symbol.StopIndex
            ?? asToken?.Symbol.StopIndex
            ?? ownerCtx.Start.StopIndex;
        int? recoveredOuterBeginIndex = bodyStartIndex >= 0
            ? FindUnmatchedBeginBefore(headerEndIndex + 1, bodyStartIndex)
            : null;
        int outerBeginIndex = recoveredOuterBeginIndex ?? bodyStartIndex;

        if (declareSpecs != null && outerBeginIndex >= 0)
        {
            int declareStart = declareSpecs.Start.StartIndex;
            string declareText = GetTrimmedSourceText(declareStart, outerBeginIndex, out int? declareEndIndex);
            if (!string.IsNullOrWhiteSpace(declareText) && declareEndIndex != null)
            {
                int declareEndLine = GetLineNumberAtIndex(declareEndIndex.Value);
                AddSubstatement(subprogram, null, ref pos, declareText,
                    "DECLARE", declareSpecs.Start.Line, declareEndLine, out _);
            }
        }

        if (outerBeginIndex >= 0)
        {
            int beginEnd = Math.Min(outerBeginIndex + "begin".Length, _sourceText.Length);
            string beginHeaderText = (outerBeginIndex >= 0 && beginEnd > outerBeginIndex)
                ? _sourceText.Substring(outerBeginIndex, beginEnd - outerBeginIndex)
                : "begin";
            int beginLine = GetLineNumberAtIndex(outerBeginIndex);
            AddSubstatement(subprogram, null, ref pos, beginHeaderText,
                "BEGIN_END", beginLine, beginLine, out int bodySeq);
            int innerPos = 0;

            if (body != null)
            {
                if (body.Start.StartIndex == outerBeginIndex)
                {
                    ExtractBodyContent(body, subprogram, bodySeq, ref innerPos);
                }
                else if (body.Start.StartIndex > outerBeginIndex)
                {
                    AddLooseStatementsFromSegment(subprogram, bodySeq, ref innerPos,
                        outerBeginIndex + "begin".Length, body.Start.StartIndex);

                    AddSubstatement(subprogram, bodySeq, ref innerPos, GetSourceText(body),
                        "BEGIN_END", body.Start.Line, body.Stop?.Line ?? body.Start.Line, out int nestedBodySeq);
                    int nestedPos = 0;
                    ExtractBodyContent(body, subprogram, nestedBodySeq, ref nestedPos);
                }
            }
        }
    }

    private int? FindUnmatchedBeginBefore(int searchStartIndex, int searchEndExclusive)
    {
        if (searchStartIndex < 0 || searchEndExclusive <= searchStartIndex || searchStartIndex >= _sourceText.Length)
            return null;

        int limit = Math.Min(searchEndExclusive, _sourceText.Length);
        var beginStack = new List<int>();
        bool inLineComment = false;
        bool inBlockComment = false;
        bool inString = false;

        for (int i = Math.Max(0, searchStartIndex); i < limit; i++)
        {
            char ch = _sourceText[i];
            char next = i + 1 < limit ? _sourceText[i + 1] : '\0';

            if (inLineComment)
            {
                if (ch == '\n')
                    inLineComment = false;
                continue;
            }

            if (inBlockComment)
            {
                if (ch == '*' && next == '/')
                {
                    inBlockComment = false;
                    i++;
                }
                continue;
            }

            if (inString)
            {
                if (ch == '\'')
                {
                    if (next == '\'')
                        i++;
                    else
                        inString = false;
                }
                continue;
            }

            if (ch == '-' && next == '-')
            {
                inLineComment = true;
                i++;
                continue;
            }

            if (ch == '/' && next == '*')
            {
                inBlockComment = true;
                i++;
                continue;
            }

            if (ch == '\'')
            {
                inString = true;
                continue;
            }

            if (IsKeywordAt(i, "BEGIN"))
            {
                beginStack.Add(i);
                i += "BEGIN".Length - 1;
                continue;
            }

            if (IsKeywordAt(i, "END"))
            {
                string? nextKeyword = ReadNextKeyword(i + "END".Length, limit);
                if (!string.Equals(nextKeyword, "IF", StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(nextKeyword, "LOOP", StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(nextKeyword, "CASE", StringComparison.OrdinalIgnoreCase))
                {
                    if (beginStack.Count > 0)
                        beginStack.RemoveAt(beginStack.Count - 1);
                }
                i += "END".Length - 1;
            }
        }

        return beginStack.Count > 0 ? beginStack[0] : null;
    }

    private void AddLooseStatementsFromSegment(
        string? subprogram,
        int? parentSeq,
        ref int position,
        int startIndex,
        int endExclusive)
    {
        if (startIndex < 0 || endExclusive <= startIndex || startIndex >= _sourceText.Length)
            return;

        int limit = Math.Min(endExclusive, _sourceText.Length);
        int currentLine = GetLineNumberAtIndex(startIndex);
        bool inLineComment = false;
        bool inBlockComment = false;
        bool inString = false;
        int? stmtStart = null;
        int stmtStartLine = currentLine;
        int? lastCodeIndex = null;
        int lastCodeLine = currentLine;

        for (int i = startIndex; i < limit; i++)
        {
            char ch = _sourceText[i];
            char next = i + 1 < limit ? _sourceText[i + 1] : '\0';

            if (inLineComment)
            {
                if (ch == '\n')
                {
                    inLineComment = false;
                    currentLine++;
                }
                continue;
            }

            if (inBlockComment)
            {
                if (ch == '*' && next == '/')
                {
                    inBlockComment = false;
                    i++;
                }
                else if (ch == '\n')
                {
                    currentLine++;
                }
                continue;
            }

            if (inString)
            {
                if (stmtStart == null)
                {
                    stmtStart = i;
                    stmtStartLine = currentLine;
                }
                lastCodeIndex = i;
                lastCodeLine = currentLine;

                if (ch == '\'')
                {
                    if (next == '\'')
                    {
                        lastCodeIndex = i + 1;
                        i++;
                    }
                    else
                    {
                        inString = false;
                    }
                }
                else if (ch == '\n')
                {
                    currentLine++;
                    lastCodeLine = currentLine - 1;
                }
                continue;
            }

            if (ch == '-' && next == '-')
            {
                inLineComment = true;
                i++;
                continue;
            }

            if (ch == '/' && next == '*')
            {
                inBlockComment = true;
                i++;
                continue;
            }

            if (char.IsWhiteSpace(ch))
            {
                if (ch == '\n')
                    currentLine++;
                continue;
            }

            if (ch == ';')
            {
                EmitLooseStatement(subprogram, parentSeq, ref position, stmtStart, stmtStartLine, lastCodeIndex, lastCodeLine);
                stmtStart = null;
                lastCodeIndex = null;
                continue;
            }

            if (stmtStart == null)
            {
                stmtStart = i;
                stmtStartLine = currentLine;
            }

            if (ch == '\'')
                inString = true;

            lastCodeIndex = i;
            lastCodeLine = currentLine;
        }

        EmitLooseStatement(subprogram, parentSeq, ref position, stmtStart, stmtStartLine, lastCodeIndex, lastCodeLine);
    }

    private void EmitLooseStatement(
        string? subprogram,
        int? parentSeq,
        ref int position,
        int? stmtStart,
        int stmtStartLine,
        int? lastCodeIndex,
        int lastCodeLine)
    {
        if (stmtStart == null || lastCodeIndex == null || lastCodeIndex < stmtStart)
            return;

        string statementText = _sourceText.Substring(stmtStart.Value, lastCodeIndex.Value - stmtStart.Value + 1).TrimEnd();
        if (string.IsNullOrWhiteSpace(statementText))
            return;

        AddSubstatement(subprogram, parentSeq, ref position, statementText,
            DetermineLooseStatementType(statementText), stmtStartLine, lastCodeLine, out _);
    }

    private static string DetermineLooseStatementType(string statementText)
    {
        string trimmed = statementText.TrimStart();
        if (trimmed.StartsWith("SELECT", StringComparison.OrdinalIgnoreCase)
            || trimmed.StartsWith("WITH", StringComparison.OrdinalIgnoreCase))
            return "SQL_SELECT";
        if (trimmed.StartsWith("UPDATE", StringComparison.OrdinalIgnoreCase))
            return "SQL_UPDATE";
        if (trimmed.StartsWith("INSERT", StringComparison.OrdinalIgnoreCase))
            return "SQL_INSERT";
        if (trimmed.StartsWith("DELETE", StringComparison.OrdinalIgnoreCase))
            return "SQL_DELETE";
        if (trimmed.StartsWith("MERGE", StringComparison.OrdinalIgnoreCase))
            return "SQL_MERGE";
        if (trimmed.StartsWith("EXECUTE IMMEDIATE", StringComparison.OrdinalIgnoreCase))
            return "SQL_EXECUTE_IMMEDIATE";
        return "OTHER";
    }

    private string GetTrimmedSourceText(int startIndex, int endExclusive, out int? trimmedEndIndex)
    {
        trimmedEndIndex = null;
        if (startIndex < 0 || endExclusive <= startIndex || startIndex >= _sourceText.Length)
            return string.Empty;

        int limit = Math.Min(endExclusive, _sourceText.Length);
        while (limit > startIndex && char.IsWhiteSpace(_sourceText[limit - 1]))
            limit--;

        if (limit <= startIndex)
            return string.Empty;

        trimmedEndIndex = limit - 1;
        return _sourceText.Substring(startIndex, limit - startIndex);
    }

    private int GetLineNumberAtIndex(int index)
    {
        if (index <= 0) return 1;

        int line = 1;
        int limit = Math.Min(index, _sourceText.Length);
        for (int i = 0; i < limit; i++)
            if (_sourceText[i] == '\n')
                line++;

        return line;
    }

    private bool IsKeywordAt(int index, string keyword)
    {
        if (index < 0 || index + keyword.Length > _sourceText.Length)
            return false;

        if (!_sourceText.AsSpan(index, keyword.Length).Equals(keyword.AsSpan(), StringComparison.OrdinalIgnoreCase))
            return false;

        int before = index - 1;
        int after = index + keyword.Length;
        bool startOk = before < 0 || !IsIdentifierChar(_sourceText[before]);
        bool endOk = after >= _sourceText.Length || !IsIdentifierChar(_sourceText[after]);
        return startOk && endOk;
    }

    private string? ReadNextKeyword(int index, int limit)
    {
        int i = index;
        while (i < limit && char.IsWhiteSpace(_sourceText[i]))
            i++;

        if (i >= limit || !char.IsLetter(_sourceText[i]))
            return null;

        int start = i;
        while (i < limit && IsIdentifierChar(_sourceText[i]))
            i++;

        return _sourceText.Substring(start, i - start);
    }

    private static bool IsIdentifierChar(char ch) =>
        char.IsLetterOrDigit(ch) || ch == '_' || ch == '$' || ch == '#';

    /// <summary>
    /// Extracts the content of a BEGIN..END block (seq_of_statements + exception handlers)
    /// as children of parentSeq, continuing from the given position counter.
    /// </summary>
    private void ExtractBodyContent(
        PlSqlParser.BodyContext body,
        string? subprogram,
        int? parentSeq,
        ref int pos)
    {
        var seqStmts = body.seq_of_statements();
        if (seqStmts != null)
            foreach (var stmt in seqStmts.statement())
                ExtractStatement(stmt, subprogram, parentSeq, ref pos);

        foreach (var handler in body.exception_handler())
        {
            var handlerThenToken = handler.THEN();
            string handlerHeaderText;
            int handlerHeaderEndLine;
            if (handlerThenToken != null)
            {
                int start = handler.Start.StartIndex;
                int end = handlerThenToken.Symbol.StopIndex;
                handlerHeaderText = (start >= 0 && end >= start && end < _sourceText.Length)
                    ? _sourceText.Substring(start, end - start + 1)
                    : GetSourceText(handler);
                handlerHeaderEndLine = handlerThenToken.Symbol.Line;
            }
            else
            {
                handlerHeaderText = GetSourceText(handler);
                handlerHeaderEndLine = handler.Stop?.Line ?? handler.Start.Line;
            }
            AddSubstatement(subprogram, parentSeq, ref pos, handlerHeaderText,
                "EXCEPTION_HANDLER", handler.Start.Line, handlerHeaderEndLine,
                out int handlerSeq);
            var handlerStmts = handler.seq_of_statements();
            if (handlerStmts != null)
                ExtractSeqStatements(handlerStmts, subprogram, handlerSeq);
        }
    }

    private void ExtractSeqStatements(
        PlSqlParser.Seq_of_statementsContext ctx,
        string? subprogram,
        int? parentSeq)
    {
        int pos = 0;
        foreach (var stmt in ctx.statement())
            ExtractStatement(stmt, subprogram, parentSeq, ref pos);
    }

    private void ExtractStatement(
        PlSqlParser.StatementContext ctx,
        string? subprogram,
        int? parentSeq,
        ref int position)
    {
        // IF
        var ifStmt = ctx.if_statement();
        if (ifStmt != null)
        {
            var ifThenToken = ifStmt.THEN();
            string ifHeaderText;
            int ifHeaderEndLine;
            if (ifThenToken != null)
            {
                int start = ifStmt.Start.StartIndex;
                int end = ifThenToken.Symbol.StopIndex;
                ifHeaderText = (start >= 0 && end >= start && end < _sourceText.Length)
                    ? _sourceText.Substring(start, end - start + 1)
                    : GetSourceText(ifStmt);
                ifHeaderEndLine = ifThenToken.Symbol.Line;
            }
            else
            {
                ifHeaderText = GetSourceText(ifStmt);
                ifHeaderEndLine = ifStmt.Stop?.Line ?? ifStmt.Start.Line;
            }
            AddSubstatement(subprogram, parentSeq, ref position, ifHeaderText,
                "IF", ifStmt.Start.Line, ifHeaderEndLine, out int ifSeq);

            int branchPos = 0;

            // THEN body: statements go directly under ifSeq (no IF_THEN wrapper)
            var thenStmts = ifStmt.seq_of_statements();
            if (thenStmts != null)
                foreach (var stmt in thenStmts.statement())
                    ExtractStatement(stmt, subprogram, ifSeq, ref branchPos);

            // ELSIF branches
            foreach (var elsif in ifStmt.elsif_part())
            {
                var elsifThenToken = elsif.THEN();
                string elsifHeaderText;
                int elsifHeaderEndLine;
                if (elsifThenToken != null)
                {
                    int start = elsif.Start.StartIndex;
                    int end = elsifThenToken.Symbol.StopIndex;
                    elsifHeaderText = (start >= 0 && end >= start && end < _sourceText.Length)
                        ? _sourceText.Substring(start, end - start + 1)
                        : GetSourceText(elsif);
                    elsifHeaderEndLine = elsifThenToken.Symbol.Line;
                }
                else
                {
                    elsifHeaderText = GetSourceText(elsif);
                    elsifHeaderEndLine = elsif.Stop?.Line ?? elsif.Start.Line;
                }
                AddSubstatement(subprogram, ifSeq, ref branchPos, elsifHeaderText,
                    "IF_ELSIF", elsif.Start.Line, elsifHeaderEndLine, out int elsifSeq);
                var elsifStmts = elsif.seq_of_statements();
                if (elsifStmts != null)
                    ExtractSeqStatements(elsifStmts, subprogram, elsifSeq);
            }

            // ELSE branch
            var elsePart = ifStmt.else_part();
            if (elsePart != null)
            {
                var elseToken = elsePart.ELSE();
                string elseHeaderText;
                int elseHeaderEndLine;
                if (elseToken != null)
                {
                    int start = elsePart.Start.StartIndex;
                    int end = elseToken.Symbol.StopIndex;
                    elseHeaderText = (start >= 0 && end >= start && end < _sourceText.Length)
                        ? _sourceText.Substring(start, end - start + 1)
                        : GetSourceText(elsePart);
                    elseHeaderEndLine = elseToken.Symbol.Line;
                }
                else
                {
                    elseHeaderText = GetSourceText(elsePart);
                    elseHeaderEndLine = elsePart.Stop?.Line ?? elsePart.Start.Line;
                }
                AddSubstatement(subprogram, ifSeq, ref branchPos, elseHeaderText,
                    "IF_ELSE", elsePart.Start.Line, elseHeaderEndLine, out int elseSeq);
                var elseStmts = elsePart.seq_of_statements();
                if (elseStmts != null)
                    ExtractSeqStatements(elseStmts, subprogram, elseSeq);
            }
            return;
        }

        // LOOP
        var loopStmt = ctx.loop_statement();
        if (loopStmt != null)
        {
            string loopType = DetermineLoopType(loopStmt);

            // Header only: from start up to and including the opening LOOP keyword
            var openLoopToken = loopStmt.LOOP(0);
            string headerText;
            int headerEndLine;
            if (openLoopToken != null)
            {
                int start = loopStmt.Start.StartIndex;
                int end = openLoopToken.Symbol.StopIndex;
                headerText = (start >= 0 && end >= start && end < _sourceText.Length)
                    ? _sourceText.Substring(start, end - start + 1)
                    : GetSourceText(loopStmt);
                headerEndLine = openLoopToken.Symbol.Line;
            }
            else
            {
                headerText = GetSourceText(loopStmt);
                headerEndLine = loopStmt.Stop?.Line ?? loopStmt.Start.Line;
            }

            AddSubstatement(subprogram, parentSeq, ref position, headerText,
                loopType, loopStmt.Start.Line, headerEndLine, out int loopSeq);

            int childPos = 0;
            var loopBody = loopStmt.seq_of_statements();
            if (loopBody != null)
                foreach (var stmt in loopBody.statement())
                    ExtractStatement(stmt, subprogram, loopSeq, ref childPos);

            return;
        }

        // FORALL
        var forallStmt = ctx.forall_statement();
        if (forallStmt != null)
        {
            AddSubstatement(subprogram, parentSeq, ref position, GetSourceText(forallStmt),
                "FORALL", forallStmt.Start.Line, forallStmt.Stop?.Line ?? forallStmt.Start.Line, out _);
            return;
        }

        // CASE
        var caseStmt = ctx.case_statement();
        if (caseStmt != null)
        {
            AddSubstatement(subprogram, parentSeq, ref position, GetSourceText(caseStmt),
                "CASE", caseStmt.Start.Line, caseStmt.Stop?.Line ?? caseStmt.Start.Line, out int caseSeq);
            ExtractCaseChildren(caseStmt, subprogram, caseSeq);
            return;
        }

        // Anonymous BEGIN..END (body)
        var body = ctx.body();
        if (body != null)
        {
            AddSubstatement(subprogram, parentSeq, ref position, GetSourceText(body),
                "BEGIN_END", body.Start.Line, body.Stop?.Line ?? body.Start.Line, out int beginSeq);
            int innerPos = 0;
            ExtractBodyContent(body, subprogram, beginSeq, ref innerPos);
            return;
        }

        // SQL statements
        var sqlStmt = ctx.sql_statement();
        if (sqlStmt != null)
        {
            string? sqlType = DetermineSqlType(sqlStmt);
            if (sqlType != null)
                AddSubstatement(subprogram, parentSeq, ref position, GetSourceText(sqlStmt),
                    sqlType, sqlStmt.Start.Line, sqlStmt.Stop?.Line ?? sqlStmt.Start.Line, out _);
            return;
        }

        // All other statements (assignment, return, raise, exit, goto, etc.)
        AddSubstatement(subprogram, parentSeq, ref position, GetSourceText(ctx),
            "OTHER", ctx.Start.Line, ctx.Stop?.Line ?? ctx.Start.Line, out _);
    }

    private static string DetermineLoopType(PlSqlParser.Loop_statementContext ctx)
    {
        if (ctx.cursor_loop_param() != null) return "LOOP_FOR";
        if (ctx.condition() != null) return "LOOP_WHILE";
        return "LOOP_BASIC";
    }

    private void ExtractCaseChildren(PlSqlParser.Case_statementContext ctx, string? subprogram, int caseSeq)
    {
        var simple = ctx.simple_case_statement();
        var searched = ctx.searched_case_statement();

        IEnumerable<PlSqlParser.Case_when_part_statementContext> whenParts;
        PlSqlParser.Case_else_part_statementContext? elsePart;

        if (simple != null)
        {
            whenParts = simple.case_when_part_statement();
            elsePart = simple.case_else_part_statement();
        }
        else if (searched != null)
        {
            whenParts = searched.case_when_part_statement();
            elsePart = searched.case_else_part_statement();
        }
        else return;

        int pos = 0;
        foreach (var when in whenParts)
        {
            AddSubstatement(subprogram, caseSeq, ref pos, GetSourceText(when),
                "CASE_WHEN", when.Start.Line, when.Stop?.Line ?? when.Start.Line, out int whenSeq);
            var stmts = when.seq_of_statements();
            if (stmts != null)
                ExtractSeqStatements(stmts, subprogram, whenSeq);
        }

        if (elsePart != null)
        {
            AddSubstatement(subprogram, caseSeq, ref pos, GetSourceText(elsePart),
                "CASE_ELSE", elsePart.Start.Line, elsePart.Stop?.Line ?? elsePart.Start.Line, out int elseSeq);
            var elseStmts = elsePart.seq_of_statements();
            if (elseStmts != null)
                ExtractSeqStatements(elseStmts, subprogram, elseSeq);
        }
    }

    private static string? DetermineSqlType(PlSqlParser.Sql_statementContext ctx)
    {
        var dml = ctx.data_manipulation_language_statements();
        if (dml != null)
        {
            if (dml.select_statement() != null) return "SQL_SELECT";
            if (dml.insert_statement() != null) return "SQL_INSERT";
            if (dml.update_statement() != null) return "SQL_UPDATE";
            if (dml.delete_statement() != null) return "SQL_DELETE";
            if (dml.merge_statement() != null) return "SQL_MERGE";
        }
        if (ctx.execute_immediate() != null) return "SQL_EXECUTE_IMMEDIATE";
        return null;
    }

    private void AddSubstatement(
        string? subprogram,
        int? parentSeq,
        ref int position,
        string sourceText,
        string statementType,
        int startLine,
        int endLine,
        out int seq)
    {
        seq = NextSeq(subprogram);
        Substatements.Add(new SubstatementInfo
        {
            Subprogram = subprogram,
            Seq = seq,
            ParentSeq = parentSeq,
            Position = position,
            StatementType = statementType,
            StartLine = startLine,
            EndLine = endLine,
            SourceText = sourceText,
        });
        position++;
    }

    // ── Call extraction ──────────────────────────────────────────────────────

    // Explicit CALL statement: CALL pkg.proc(...) or CALL proc(...)
    public override object? VisitCall_statement([NotNull] PlSqlParser.Call_statementContext ctx)
    {
        var routineNames = ctx.routine_name();
        if (routineNames.Length > 0)
            ExtractCallFromRoutineName(routineNames[0]);
        return base.VisitCall_statement(ctx);
    }

    private void ExtractCallFromRoutineName(PlSqlParser.Routine_nameContext rn)
    {
        var parts = new List<string>();

        var ident = rn.identifier()?.GetText()?.ToUpperInvariant();
        if (!string.IsNullOrEmpty(ident))
            parts.Add(ident);

        foreach (var idExpr in rn.id_expression())
        {
            var part = idExpr.GetText().ToUpperInvariant();
            if (!string.IsNullOrEmpty(part))
                parts.Add(part);
        }

        var (calleeSchema, calleeObject, calleeSubprogram) = ResolveCallTarget(parts);
        if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
            AddEdge(calleeSchema, calleeObject, calleeSubprogram);
    }

    // Inline function call in expression: PKG.FUNC(args) or FUNC(args)
    public override object? VisitGeneral_element([NotNull] PlSqlParser.General_elementContext ctx)
    {
        var parts = ctx.general_element_part();
        // Only process at this level if the last part has function arguments
        // (to avoid double-counting on recursive general_element)
        if (parts.Length > 0 && parts[^1].function_argument().Length > 0)
        {
            var nameParts = new List<string>();
            CollectGeneralElementNameParts(ctx, nameParts);

            var (calleeSchema, calleeObject, calleeSubprogram) = ResolveCallTarget(nameParts);
            if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
                AddEdge(calleeSchema, calleeObject, calleeSubprogram);
        }
        return base.VisitGeneral_element(ctx);
    }

    private static void CollectGeneralElementNameParts(
        PlSqlParser.General_elementContext ctx,
        List<string> nameParts)
    {
        var parent = ctx.general_element();
        if (parent != null)
            CollectGeneralElementNameParts(parent, nameParts);

        foreach (var part in ctx.general_element_part())
        {
            var name = part.id_expression()?.GetText()?.ToUpperInvariant();
            if (!string.IsNullOrEmpty(name))
                nameParts.Add(name);
        }
    }

    private static (string? CalleeSchema, string CalleeObject, string? CalleeSubprogram) ResolveCallTarget(
        IReadOnlyList<string> parts)
    {
        if (parts.Count == 0)
            return (null, string.Empty, null);

        if (parts.Count == 1)
            return (null, parts[0], null);

        if (parts.Count == 2)
            return (null, parts[0], parts[1]);

        return (parts[^3], parts[^2], parts[^1]);
    }

    private void AddEdge(string? calleeSchema, string calleeObject, string? calleeSubprogram)
    {
        var key = $"{CurrentSubprogram}|{calleeSchema}|{calleeObject}|{calleeSubprogram}";
        if (!_edgeKeys.Add(key)) return;

        CallEdges.Add(new CallEdge
        {
            CallerSubprogram = CurrentSubprogram,
            CalleeSchema = calleeSchema,
            CalleeObject = calleeObject,
            CalleeSubprogram = calleeSubprogram,
        });
    }

    // ── DML operation tracking ───────────────────────────────────────────────

    public override object? VisitSelect_statement([NotNull] PlSqlParser.Select_statementContext ctx)
    {
        _dmlStack.Push("SELECT");
        var result = base.VisitSelect_statement(ctx);
        _dmlStack.Pop();
        return result;
    }

    public override object? VisitInsert_statement([NotNull] PlSqlParser.Insert_statementContext ctx)
    {
        _dmlStack.Push("INSERT");
        var result = base.VisitInsert_statement(ctx);
        _dmlStack.Pop();
        return result;
    }

    public override object? VisitUpdate_statement([NotNull] PlSqlParser.Update_statementContext ctx)
    {
        _dmlStack.Push("UPDATE");
        var result = base.VisitUpdate_statement(ctx);
        _dmlStack.Pop();
        return result;
    }

    public override object? VisitDelete_statement([NotNull] PlSqlParser.Delete_statementContext ctx)
    {
        _dmlStack.Push("DELETE");
        var result = base.VisitDelete_statement(ctx);
        _dmlStack.Pop();
        return result;
    }

    // MERGE target table is a direct tableview_name on the merge_statement node;
    // handle it before recursing so the USING subquery (SELECT) doesn't inherit "MERGE"
    public override object? VisitMerge_statement([NotNull] PlSqlParser.Merge_statementContext ctx)
    {
        var tv = ctx.tableview_name();
        if (tv != null)
            RecordTableAccess(tv, "MERGE");
        // Recurse into the rest (USING clause may contain a SELECT)
        return base.VisitMerge_statement(ctx);
    }

    // dml_table_expression_clause appears in FROM clauses and as DML targets
    public override object? VisitDml_table_expression_clause(
        [NotNull] PlSqlParser.Dml_table_expression_clauseContext ctx)
    {
        var tv = ctx.tableview_name();
        if (tv != null && CurrentDml != null)
            RecordTableAccess(tv, CurrentDml);
        return base.VisitDml_table_expression_clause(ctx);
    }

    private void RecordTableAccess(PlSqlParser.Tableview_nameContext tv, string operation)
    {
        var rawText = tv.GetText().ToUpperInvariant();
        // Strip @dblink suffix if present
        var atIdx = rawText.IndexOf('@');
        if (atIdx >= 0) rawText = rawText[..atIdx];

        string? tableSchema = null;
        string tableName;
        var dot = rawText.IndexOf('.');
        if (dot >= 0)
        {
            tableSchema = rawText[..dot];
            tableName = rawText[(dot + 1)..];
        }
        else
        {
            tableName = rawText;
        }

        if (tableName is "DUAL" or "" ) return;

        var key = $"{CurrentSubprogram}|{tableName}|{operation}";
        if (!_accessKeys.Add(key)) return;

        TableAccesses.Add(new TableAccess
        {
            Subprogram = CurrentSubprogram,
            TableSchema = tableSchema,
            TableName = tableName,
            Operation = operation,
        });
    }
}
