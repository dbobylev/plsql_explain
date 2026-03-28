using System.Collections.Generic;
using Antlr4.Runtime;
using Antlr4.Runtime.Misc;
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
public partial class PlsqlVisitor : PlSqlParserBaseVisitor<object?>
{
    // Standard Oracle packages and built-in functions that are part of Oracle's
    // standard library; calls to these are excluded from the call graph.
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

    // Stack of enclosing subprogram names (null = package level or standalone object).
    private readonly Stack<string?> _subprogramStack = new();
    private string? CurrentSubprogram => _subprogramStack.Count > 0 ? _subprogramStack.Peek() : null;

    // Stack of current DML operation for table access tracking.
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

    // ── Subprogram boundary tracking ────────────────────────────────────────

    // Called for subprograms INSIDE a package body.
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

    /// <summary>Appends a <see cref="SubprogramInfo"/> record for the given context node.</summary>
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
}
