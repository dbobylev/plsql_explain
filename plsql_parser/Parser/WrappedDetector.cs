using System.Text.RegularExpressions;

namespace PlsqlParser.Parser;

public static class WrappedDetector
{
    // Oracle WRAPPED header: one or more words/digits/underscores followed by " wrapped"
    // e.g. "package body wrapped" or "pkgname wrapped"
    private static readonly Regex WrappedPattern =
        new(@"^\s*\w+(\s+\w+)*\s+wrapped\b", RegexOptions.IgnoreCase | RegexOptions.Multiline);

    public static bool IsWrapped(string sourceText)
    {
        // Check only the first ~200 chars for performance
        var sample = sourceText.Length > 200 ? sourceText[..200] : sourceText;
        return WrappedPattern.IsMatch(sample);
    }
}
