using System;
using System.Text.Json;
using Antlr4.Runtime;
using PlsqlParser.Grammar;
using PlsqlParser.Model;
using PlsqlParser.Parser;

var inputJson = Console.In.ReadToEnd();

ParseInput input;
try
{
    input = JsonSerializer.Deserialize<ParseInput>(inputJson)
        ?? throw new InvalidOperationException("Null deserialization result");
}
catch (Exception ex)
{
    Console.Error.WriteLine($"Failed to parse input JSON: {ex.Message}");
    Environment.Exit(1);
    return;
}

var output = new ParseOutput
{
    SchemaName = input.SchemaName,
    ObjectName = input.ObjectName,
    ObjectType = input.ObjectType,
};

if (WrappedDetector.IsWrapped(input.SourceText))
{
    output.Status = "wrapped";
    Console.WriteLine(JsonSerializer.Serialize(output));
    return;
}

try
{
    var inputStream = new AntlrInputStream(input.SourceText);
    var lexer = new PlSqlLexer(inputStream);
    lexer.RemoveErrorListeners();

    var tokenStream = new CommonTokenStream(lexer);
    var parser = new PlSqlParser(tokenStream);
    parser.RemoveErrorListeners();

    var errorListener = new ErrorCollector();
    parser.AddErrorListener(errorListener);

    var tree = parser.sql_script();

    if (errorListener.HasErrors)
    {
        output.Status = "error";
        output.ErrorMessage = errorListener.FirstError;
        Console.WriteLine(JsonSerializer.Serialize(output));
        return;
    }

    var visitor = new PlsqlVisitor(input.SchemaName, input.ObjectName, input.ObjectType, input.SourceText);
    visitor.Visit(tree);

    output.Status = "ok";
    output.CallEdges = visitor.CallEdges;
    output.TableAccesses = visitor.TableAccesses;
    output.Subprograms = visitor.Subprograms;
    output.Substatements = visitor.Substatements;
}
catch (Exception ex)
{
    output.Status = "error";
    output.ErrorMessage = ex.Message;
}

Console.WriteLine(JsonSerializer.Serialize(output));
