using Antlr4.Runtime;

namespace PlsqlParser.Parser;

// ── Source text / character-level utilities ──────────────────────────────
//
// All methods here operate directly on _sourceText by character index,
// independent of the ANTLR parse tree structure.

public partial class PlsqlVisitor
{
    /// <summary>
    /// Returns the raw source slice for <paramref name="ctx"/> using character indices.
    /// Falls back to ctx.GetText() if indices are out of range.
    /// </summary>
    private string GetSourceText(ParserRuleContext ctx)
    {
        if (ctx.Stop == null) return ctx.GetText();
        int start = ctx.Start.StartIndex;
        int end = ctx.Stop.StopIndex;
        if (start < 0 || end < start || end >= _sourceText.Length) return ctx.GetText();
        return _sourceText.Substring(start, end - start + 1);
    }

    /// <summary>
    /// Extracts the source slice [startIndex, endExclusive) trimming trailing whitespace.
    /// Returns the trimmed end index via <paramref name="trimmedEndIndex"/>.
    /// </summary>
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

    /// <summary>Returns the 1-based line number at the given character index.</summary>
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

    /// <summary>
    /// True if <paramref name="keyword"/> appears at <paramref name="index"/>
    /// as a whole word (not part of a longer identifier).
    /// </summary>
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
    /// Scans [searchStartIndex, searchEndExclusive) for a BEGIN keyword whose matching END
    /// lies outside the scanned range, respecting line comments, block comments, and string literals.
    /// Returns the index of the first such unmatched BEGIN, or null if none is found.
    /// Used to locate an outer BEGIN block that encloses a subprogram body.
    /// </summary>
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

    /// <summary>
    /// Walks a raw source segment [startIndex, endExclusive) character-by-character,
    /// splitting on semicolons to emit individual "loose" statements that fall between
    /// parse-tree nodes. Respects comments and string literals.
    /// </summary>
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
}
