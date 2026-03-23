using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace PlsqlParser.Model;

public class CallEdge
{
    [JsonPropertyName("caller_subprogram")]
    public string? CallerSubprogram { get; set; }

    [JsonPropertyName("callee_schema")]
    public string? CalleeSchema { get; set; }

    [JsonPropertyName("callee_object")]
    public string CalleeObject { get; set; } = string.Empty;

    [JsonPropertyName("callee_subprogram")]
    public string? CalleeSubprogram { get; set; }
}

public class TableAccess
{
    [JsonPropertyName("subprogram")]
    public string? Subprogram { get; set; }

    [JsonPropertyName("table_schema")]
    public string? TableSchema { get; set; }

    [JsonPropertyName("table_name")]
    public string TableName { get; set; } = string.Empty;

    [JsonPropertyName("operation")]
    public string Operation { get; set; } = string.Empty;
}

public class ParseInput
{
    [JsonPropertyName("schema_name")]
    public string SchemaName { get; set; } = string.Empty;

    [JsonPropertyName("object_name")]
    public string ObjectName { get; set; } = string.Empty;

    [JsonPropertyName("object_type")]
    public string ObjectType { get; set; } = string.Empty;

    [JsonPropertyName("source_text")]
    public string SourceText { get; set; } = string.Empty;
}

public class ParseOutput
{
    [JsonPropertyName("schema_name")]
    public string SchemaName { get; set; } = string.Empty;

    [JsonPropertyName("object_name")]
    public string ObjectName { get; set; } = string.Empty;

    [JsonPropertyName("object_type")]
    public string ObjectType { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; set; }

    [JsonPropertyName("call_edges")]
    public List<CallEdge> CallEdges { get; set; } = new();

    [JsonPropertyName("table_accesses")]
    public List<TableAccess> TableAccesses { get; set; } = new();
}
