using System.IO;
using Antlr4.Runtime;

namespace PlsqlParser.Parser;

/// <summary>
/// Collects ANTLR4 syntax errors without writing to stderr.
/// </summary>
public class ErrorCollector : BaseErrorListener
{
    public bool HasErrors { get; private set; }
    public string? FirstError { get; private set; }

    public override void SyntaxError(
        TextWriter output,
        IRecognizer recognizer,
        IToken offendingSymbol,
        int line,
        int charPositionInLine,
        string msg,
        RecognitionException e)
    {
        if (!HasErrors)
        {
            FirstError = $"line {line}:{charPositionInLine} {msg}";
            HasErrors = true;
        }
    }
}
