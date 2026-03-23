using System;
using System.Collections.Generic;
using Antlr4.Runtime.Misc;
using PlsqlParser.Grammar;
using PlsqlParser.Model;

namespace PlsqlParser.Parser;

/// <summary>
/// Visits a PL/SQL parse tree (grammars-v4 PlSql grammar) and extracts
/// call edges and table access entries.
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

    // Stack of enclosing subprogram names (null = package level or standalone object)
    private readonly Stack<string?> _subprogramStack = new();
    private string? CurrentSubprogram => _subprogramStack.Count > 0 ? _subprogramStack.Peek() : null;

    // Stack of current DML operation for table access tracking
    private readonly Stack<string> _dmlStack = new();
    private string? CurrentDml => _dmlStack.Count > 0 ? _dmlStack.Peek() : null;

    public List<CallEdge> CallEdges { get; } = new();
    public List<TableAccess> TableAccesses { get; } = new();

    private readonly HashSet<string> _edgeKeys = new();
    private readonly HashSet<string> _accessKeys = new();

    public PlsqlVisitor(string callerSchema, string callerObject, string callerType)
    {
        _callerSchema = callerSchema;
        _callerObject = callerObject;
        _callerType = callerType;
        _subprogramStack.Push(null);
    }

    // ── Subprogram boundary tracking ────────────────────────────────────────

    // Called for subprograms INSIDE a package body
    public override object? VisitProcedure_body([NotNull] PlSqlParser.Procedure_bodyContext ctx)
    {
        var name = ctx.identifier()?.GetText()?.ToUpperInvariant();
        _subprogramStack.Push(name);
        var result = base.VisitProcedure_body(ctx);
        _subprogramStack.Pop();
        return result;
    }

    public override object? VisitFunction_body([NotNull] PlSqlParser.Function_bodyContext ctx)
    {
        var name = ctx.identifier()?.GetText()?.ToUpperInvariant();
        _subprogramStack.Push(name);
        var result = base.VisitFunction_body(ctx);
        _subprogramStack.Pop();
        return result;
    }

    // For top-level CREATE PROCEDURE / CREATE FUNCTION, caller_subprogram stays null —
    // no push needed; the root null on the stack is used.

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
        var ident = rn.identifier()?.GetText()?.ToUpperInvariant();
        var idExprs = rn.id_expression();

        string calleeObject;
        string? calleeSubprogram = null;

        if (idExprs.Length == 0)
        {
            // bare call: PROC(...)
            calleeObject = ident ?? string.Empty;
        }
        else if (idExprs.Length == 1)
        {
            // PKG.PROC(...)
            calleeObject = ident ?? string.Empty;
            calleeSubprogram = idExprs[0].GetText().ToUpperInvariant();
        }
        else
        {
            // SCHEMA.PKG.PROC(...) — skip schema, take last two parts
            calleeObject = idExprs[^2].GetText().ToUpperInvariant();
            calleeSubprogram = idExprs[^1].GetText().ToUpperInvariant();
        }

        if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
            AddEdge(calleeObject, calleeSubprogram);
    }

    // Inline function call in expression: PKG.FUNC(args) or FUNC(args)
    public override object? VisitGeneral_element([NotNull] PlSqlParser.General_elementContext ctx)
    {
        var parts = ctx.general_element_part();
        // Only process at this level if the last part has function arguments
        // (to avoid double-counting on recursive general_element)
        if (parts.Length > 0 && parts[^1].function_argument().Length > 0)
        {
            string calleeObject;
            string? calleeSubprogram = null;

            if (parts.Length == 1)
            {
                calleeObject = parts[0].id_expression()?.GetText()?.ToUpperInvariant() ?? string.Empty;
            }
            else
            {
                // Take second-to-last as object, last as subprogram (handles schema.pkg.proc)
                calleeObject = parts[^2].id_expression()?.GetText()?.ToUpperInvariant() ?? string.Empty;
                calleeSubprogram = parts[^1].id_expression()?.GetText()?.ToUpperInvariant();
            }

            if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
                AddEdge(calleeObject, calleeSubprogram);
        }
        return base.VisitGeneral_element(ctx);
    }

    private void AddEdge(string calleeObject, string? calleeSubprogram)
    {
        var key = $"{CurrentSubprogram}|{calleeObject}|{calleeSubprogram}";
        if (!_edgeKeys.Add(key)) return;

        CallEdges.Add(new CallEdge
        {
            CallerSubprogram = CurrentSubprogram,
            CalleeSchema = null,
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
