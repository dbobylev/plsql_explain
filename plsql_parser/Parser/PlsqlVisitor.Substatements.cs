using System.Collections.Generic;
using Antlr4.Runtime;
using Antlr4.Runtime.Tree;
using PlsqlParser.Grammar;
using PlsqlParser.Model;

namespace PlsqlParser.Parser;

// ── Substatement extraction (parse-tree driven) ──────────────────────────
//
// Walks grammar nodes to build the hierarchical Substatements list.
// Children are always summarized before their parents (post-order for leaves).

public partial class PlsqlVisitor
{
    private int NextSeq(string? subprogram)
    {
        var key = subprogram ?? "";
        if (!_seqCounters.TryGetValue(key, out var current))
            current = 0;
        _seqCounters[key] = current + 1;
        return current;
    }

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

                    int nestedBodyStart = body.Start.StartIndex;
                    int nestedBeginEnd = Math.Min(nestedBodyStart + "begin".Length, _sourceText.Length);
                    string nestedBeginHeaderText = (nestedBodyStart >= 0 && nestedBeginEnd > nestedBodyStart && nestedBodyStart < _sourceText.Length)
                        ? _sourceText.Substring(nestedBodyStart, nestedBeginEnd - nestedBodyStart)
                        : "begin";
                    AddSubstatement(subprogram, bodySeq, ref innerPos, nestedBeginHeaderText,
                        "BEGIN_END", body.Start.Line, body.Start.Line, out int nestedBodySeq);
                    int nestedPos = 0;
                    ExtractBodyContent(body, subprogram, nestedBodySeq, ref nestedPos);
                }
            }
        }
    }

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
        // Dispatch to the appropriate handler based on which grammar alternative is present.
        // Each branch returns early after recording the node to avoid double-counting.

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
            int bodyStart = body.Start.StartIndex;
            int beginEnd = Math.Min(bodyStart + "begin".Length, _sourceText.Length);
            string beginHeaderText = (bodyStart >= 0 && beginEnd > bodyStart && bodyStart < _sourceText.Length)
                ? _sourceText.Substring(bodyStart, beginEnd - bodyStart)
                : "begin";
            AddSubstatement(subprogram, parentSeq, ref position, beginHeaderText,
                "BEGIN_END", body.Start.Line, body.Start.Line, out int beginSeq);
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

    /// <summary>Maps a sql_statement grammar node to a SQL_* type string, or null for unsupported variants.</summary>
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

    /// <summary>
    /// Assigns the next global sequence number for <paramref name="subprogram"/>,
    /// creates a <see cref="SubstatementInfo"/> record, and appends it to <see cref="Substatements"/>.
    /// </summary>
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
}
