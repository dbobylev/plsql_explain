# ANTLR4 Grammar Sources

Place the generated C# sources for the PL/SQL grammar here.

## How to generate

1. Download `PlSqlLexer.g4` and `PlSqlParser.g4` from:
   https://github.com/antlr/grammars-v4/tree/master/sql/plsql

2. Run ANTLR4 to generate C# sources:
   ```
   antlr4 -Dlanguage=CSharp -package PlsqlParser.Grammar -o . PlSqlLexer.g4 PlSqlParser.g4
   ```

3. The generated files will be picked up automatically by the .csproj.

## Required files

- `PlSqlLexer.cs`
- `PlSqlLexer.interp`
- `PlSqlLexer.tokens`
- `PlSqlParser.cs`
- `PlSqlParser.interp`
- `PlSqlParser.tokens`
- `PlSqlParserBaseVisitor.cs`
- `PlSqlParserVisitor.cs`
