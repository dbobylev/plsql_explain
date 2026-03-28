using System.Collections.Generic;
using Antlr4.Runtime.Misc;
using PlsqlParser.Grammar;
using PlsqlParser.Model;

namespace PlsqlParser.Parser;

// ── Call edge extraction ─────────────────────────────────────────────────
//
// Handles two syntactic forms of calls:
//   1. Explicit CALL statement:   CALL pkg.proc(...)
//   2. Inline function call:      result := pkg.func(args)

public partial class PlsqlVisitor
{
    /// <summary>Handles explicit CALL statements: <c>CALL pkg.proc(...)</c> or <c>CALL proc(...)</c>.</summary>
    public override object? VisitCall_statement([NotNull] PlSqlParser.Call_statementContext ctx)
    {
        var routineNames = ctx.routine_name();
        if (routineNames.Length > 0)
            ExtractCallFromRoutineName(routineNames[0]);
        return base.VisitCall_statement(ctx);
    }

    private void ExtractCallFromRoutineName(PlSqlParser.Routine_nameContext rn)
    {
        var parts = new List<string>();

        var ident = rn.identifier()?.GetText()?.ToUpperInvariant();
        if (!string.IsNullOrEmpty(ident))
            parts.Add(ident);

        foreach (var idExpr in rn.id_expression())
        {
            var part = idExpr.GetText().ToUpperInvariant();
            if (!string.IsNullOrEmpty(part))
                parts.Add(part);
        }

        var (calleeSchema, calleeObject, calleeSubprogram) = ResolveCallTarget(parts);
        if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
            AddEdge(calleeSchema, calleeObject, calleeSubprogram);
    }

    /// <summary>
    /// Handles inline function calls in expressions, e.g. <c>PKG.FUNC(args)</c>.
    /// Only fires when the innermost element part carries function arguments,
    /// preventing double-counting on recursive general_element nodes.
    /// </summary>
    public override object? VisitGeneral_element([NotNull] PlSqlParser.General_elementContext ctx)
    {
        var parts = ctx.general_element_part();
        if (parts.Length > 0 && parts[^1].function_argument().Length > 0)
        {
            var nameParts = new List<string>();
            CollectGeneralElementNameParts(ctx, nameParts);

            var (calleeSchema, calleeObject, calleeSubprogram) = ResolveCallTarget(nameParts);
            if (!string.IsNullOrEmpty(calleeObject) && !BuiltinPackages.Contains(calleeObject))
                AddEdge(calleeSchema, calleeObject, calleeSubprogram);
        }
        return base.VisitGeneral_element(ctx);
    }

    private static void CollectGeneralElementNameParts(
        PlSqlParser.General_elementContext ctx,
        List<string> nameParts)
    {
        var parent = ctx.general_element();
        if (parent != null)
            CollectGeneralElementNameParts(parent, nameParts);

        foreach (var part in ctx.general_element_part())
        {
            var name = part.id_expression()?.GetText()?.ToUpperInvariant();
            if (!string.IsNullOrEmpty(name))
                nameParts.Add(name);
        }
    }

    /// <summary>
    /// Resolves a dot-separated name list into (schema, object, subprogram).
    /// 1 part → unqualified call; 2 parts → pkg.proc; 3+ parts → schema.pkg.proc (last 3 win).
    /// </summary>
    private static (string? CalleeSchema, string CalleeObject, string? CalleeSubprogram) ResolveCallTarget(
        IReadOnlyList<string> parts)
    {
        if (parts.Count == 0)
            return (null, string.Empty, null);

        if (parts.Count == 1)
            return (null, parts[0], null);

        if (parts.Count == 2)
            return (null, parts[0], parts[1]);

        return (parts[^3], parts[^2], parts[^1]);
    }

    /// <summary>Deduplicates and appends a <see cref="CallEdge"/> keyed on (CurrentSubprogram, callee triple).</summary>
    private void AddEdge(string? calleeSchema, string calleeObject, string? calleeSubprogram)
    {
        var key = $"{CurrentSubprogram}|{calleeSchema}|{calleeObject}|{calleeSubprogram}";
        if (!_edgeKeys.Add(key)) return;

        CallEdges.Add(new CallEdge
        {
            CallerSubprogram = CurrentSubprogram,
            CalleeSchema = calleeSchema,
            CalleeObject = calleeObject,
            CalleeSubprogram = calleeSubprogram,
        });
    }
}
