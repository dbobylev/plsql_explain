using Antlr4.Runtime.Misc;
using PlsqlParser.Grammar;
using PlsqlParser.Model;

namespace PlsqlParser.Parser;

// ── DML / table access tracking ──────────────────────────────────────────
//
// DML visitors push the current operation name onto _dmlStack before recursing
// so that nested tableview_name nodes can read the operation from CurrentDml.

public partial class PlsqlVisitor
{
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

    /// <summary>
    /// MERGE target is a direct tableview_name on the merge_statement node.
    /// Handled before recursion so the USING subquery (a SELECT) does not inherit "MERGE".
    /// </summary>
    public override object? VisitMerge_statement([NotNull] PlSqlParser.Merge_statementContext ctx)
    {
        var tv = ctx.tableview_name();
        if (tv != null)
            RecordTableAccess(tv, "MERGE");
        return base.VisitMerge_statement(ctx);
    }

    /// <summary>Records the table referenced in a FROM clause or as a DML target.</summary>
    public override object? VisitDml_table_expression_clause(
        [NotNull] PlSqlParser.Dml_table_expression_clauseContext ctx)
    {
        var tv = ctx.tableview_name();
        if (tv != null && CurrentDml != null)
            RecordTableAccess(tv, CurrentDml);
        return base.VisitDml_table_expression_clause(ctx);
    }

    /// <summary>
    /// Parses the raw tableview_name text, strips any @dblink suffix,
    /// separates optional schema qualifier, and appends a deduplicated <see cref="TableAccess"/> record.
    /// </summary>
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

        if (tableName is "DUAL" or "") return;

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
