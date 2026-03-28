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
    output.Substatements = MergeAdjacentOthers(visitor.Substatements);
}
catch (Exception ex)
{
    output.Status = "error";
    output.ErrorMessage = ex.Message;
}

Console.WriteLine(JsonSerializer.Serialize(output));

static List<SubstatementInfo> MergeAdjacentOthers(List<SubstatementInfo> input)
{
    // Group siblings by (subprogram, parent_seq), find consecutive OTHER runs,
    // merge each run into one entry, then re-assign Position sequentially.
    var groups = new Dictionary<(string, int), List<SubstatementInfo>>();
    var groupOrder = new List<(string, int)>();

    foreach (var s in input)
    {
        var key = (s.Subprogram ?? "", s.ParentSeq ?? -1);
        if (!groups.ContainsKey(key))
        {
            groups[key] = new List<SubstatementInfo>();
            groupOrder.Add(key);
        }
        groups[key].Add(s);
    }

    var merged = new Dictionary<(string, int), List<SubstatementInfo>>();
    foreach (var key in groupOrder)
    {
        var siblings = groups[key];
        siblings.Sort((a, b) => a.Position.CompareTo(b.Position));

        var result = new List<SubstatementInfo>();
        int i = 0;
        while (i < siblings.Count)
        {
            var cur = siblings[i];
            if (cur.StatementType != "OTHER")
            {
                result.Add(cur);
                i++;
                continue;
            }
            // Collect consecutive OTHER run.
            int runStart = i;
            while (i < siblings.Count && siblings[i].StatementType == "OTHER")
                i++;
            int runEnd = i - 1;

            if (runStart == runEnd)
            {
                result.Add(siblings[runStart]);
            }
            else
            {
                var first = siblings[runStart];
                var texts = new System.Text.StringBuilder();
                for (int j = runStart; j <= runEnd; j++)
                {
                    if (j > runStart) texts.Append('\n');
                    texts.Append(siblings[j].SourceText);
                }
                result.Add(new SubstatementInfo
                {
                    Subprogram    = first.Subprogram,
                    Seq           = first.Seq,
                    ParentSeq     = first.ParentSeq,
                    Position      = 0,  // re-assigned below
                    StatementType = "OTHER",
                    StartLine     = first.StartLine,
                    EndLine       = siblings[runEnd].EndLine,
                    SourceText    = texts.ToString(),
                });
            }
        }

        // Re-assign Position values (0, 1, 2, …) after merging.
        for (int p = 0; p < result.Count; p++)
            result[p].Position = p;

        merged[key] = result;
    }

    var flat = new List<SubstatementInfo>();
    foreach (var key in groupOrder)
        flat.AddRange(merged[key]);
    return flat;
}
