# Problem statement authoring guide

## Language

These authoring instructions are written in English. This does not determine the
language of the problem statement.

- Follow the statement language explicitly requested in the user's prompt.
- When editing or importing an existing statement, preserve its language unless
  the user requests translation.
- If the intended language is unclear, ask the user before composing, translating
  or publishing the statement. Do not default to English or Vietnamese merely
  because this guide or its examples use that language.
- Keep prose and section headings consistent with the chosen language, except
  sample labels: always keep `Input 1`, `Output 1`, etc. in English. Preserve
  identifiers, filenames, formulas and literal sample data.

## Basic Markdown

Write the problem's `description` as raw Markdown. Use
[CommonMark](https://commonmark.org/help/) as the reference for basic syntax:
headings, paragraphs, emphasis, lists, links, images and code blocks. The site has
its own renderer and math extension; this reference is not a claim of full
CommonMark conformance.

Do not wrap the entire statement in a code block. Put a space after heading
markers: `## Input`, not `##Input`. Use blank lines between paragraphs and around
lists, headings and fenced code blocks.

## Math and variables

Use **one tilde on each side**: `~...~`. The content uses LaTeX math syntax and is
rendered by MathJax. Use this for variable names in prose as well as expressions,
so both have consistent mathematical typography.

| Purpose | Markdown source |
| --- | --- |
| Variables | `~n~`, `~i~` |
| Subscripts | `~a_i~`, `~a_{i+1}~` |
| Expressions | `~a+b~` |
| Constraints | `~1 \le n \le 10^5~` |
| Fractions | `~\frac{a}{b}~` |
| Percentages in math | `~50\%~` |

Example in Vietnamese (use this language only when appropriate to the prompt):

```markdown
- Dòng đầu ghi số ~n~.
- ~n~ dòng tiếp theo, dòng thứ ~i~ ghi số ~a_i~.
```

`~~...~~` is strikethrough, not math. Do not put math delimiters in inline code in
the actual statement; backticks here illustrate the source syntax. Math formatting
does not require enabling `is_full_markup`.

## Statement structure

The user's two reference statements demonstrate these supported layouts:

1. Narrative, `## Yêu cầu`, `## Input`, `## Output`, `## Sample Test`,
   then `## Ràng buộc` for scoring subtasks.
2. Short introduction, `## Yêu cầu`, `## Dữ liệu`, `## Kết quả`,
   `## Giới hạn`, then `## Ví dụ`, including named input/output files.

These are examples, not a requirement to use Vietnamese headings or identical
section names. Follow the user's chosen language and layout. When the user has
not specified a layout, organize the statement into an introduction, task, input,
output, constraints and examples. Keep scoring subtasks distinct from overall
constraints. Do not invent missing limits, scoring percentages or requirements.

Keep file-based I/O filenames exactly as provided, such as **FIBOC.INP** and
**FIBOC.OUT**. Do not silently convert file-based I/O to standard input/output.
The narrative description does not configure the judge's actual I/O mode.

## Sample input and output

Use separate fenced code blocks for each input and output. Preserve values,
spaces and line breaks; do not use math delimiters inside the blocks. Number
multiple examples consistently: pair `### Input 1` with `### Output 1`, then
`### Input 2` with `### Output 2`, and so on. Always use these English labels,
even when the statement is in another language; do not translate `Input` or
`Output`. If the source omits example numbers, as in the Fibonacci reference,
add consecutive numbers in source order while preserving input/output pairing
and all sample data. This is a formatting correction; it does not require asking
the user to confirm numbering. A valid English layout is:

````markdown
## Examples

### Input 1

```
10
```

### Output 1

```
1 1 2 3 5 8
```
````

## Source validation

Check sample outputs against the task before publishing. Formatting corrections
must not silently change the problem's meaning. If the source has inconsistent
examples, ambiguous conventions or missing essential information, explain the
issue and ask the user to confirm the intended content before uploading it.

For example, the user's Fibonacci source specifies the sequence starting
`1 1 2 3 5 8`. Its sample for `N = 50` ends in `13 31 24`, which is inconsistent
with that sequence. The expected sequence is `1 1 2 3 5 8 13 21 34`; treat this as
a proposed correction requiring confirmation, not an output to copy unchanged
or silently replace in a published problem. The two supplied problems are format
references, not authorization to create database records.

## Sending Markdown through MCP

Use a JSON serializer to preserve newlines, tildes and LaTeX backslashes. For
example, the text `~1 \le n~` is represented in JSON as `"~1 \\le n~"`.
