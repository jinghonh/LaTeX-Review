% Minimal distributable style for this synthetic article-only fixture.
% Intentionally self-contained: no TeX distribution style lookup is needed.
ENTRY { author title journal year } {} {}

FUNCTION {begin.bib}
{ "\begin{thebibliography}{99}" write$ newline$ }

FUNCTION {end.bib}
{ "\end{thebibliography}" write$ newline$ }

FUNCTION {article}
{ "\bibitem{" write$ cite$ write$ "}" write$ newline$
  author empty$ { skip$ } { author write$ ". " write$ } if$
  title empty$ { skip$ } { title write$ ". " write$ } if$
  journal empty$ { skip$ } { journal write$ ". " write$ } if$
  year empty$ { skip$ } { year write$ } if$
  newline$
}

FUNCTION {default.type} { article }
READ
EXECUTE {begin.bib}
ITERATE {call.type$}
EXECUTE {end.bib}
