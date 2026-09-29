"""结构识别与预览共用的常见 LaTeX 命令口径。"""

REFERENCE_COMMANDS = frozenset({"ref", "eqref", "autoref", "pageref", "cref", "Cref"})

MATH_COMMANDS = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta",
    "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
    "sum", "prod", "int", "iint", "oint", "frac", "dfrac", "tfrac", "binom", "sqrt", "left", "right",
    "bigl", "bigr", "Bigl", "Bigr", "big", "Big", "bigg", "Bigg", "mathrm", "mathbf", "mathbb", "mathcal",
    "mathit", "mathsf", "mathfrak", "boldsymbol", "bm", "text", "textnormal", "operatorname", "overline",
    "underline", "widehat", "widetilde", "hat", "bar", "tilde", "vec", "dot", "ddot", "underbrace", "overbrace",
    "cdot", "times", "pm", "mp", "le", "leq", "ge", "geq", "neq", "approx", "sim", "simeq", "equiv",
    "in", "notin", "subset", "subseteq", "supset", "supseteq", "cup", "cap", "bigcap", "emptyset", "forall", "exists",
    "parallel", "succ", "prec", "succeq", "preceq", "rm",
    "infty", "partial", "nabla", "ell", "top", "bot", "sup", "inf", "min", "max", "lim", "limsup",
    "liminf", "sin", "cos", "tan", "log", "ln", "exp", "det", "dim", "ker", "arg", "deg", "Pr",
    "rightarrow", "leftarrow", "longrightarrow", "longmapsto", "mapsto", "Rightarrow", "Leftrightarrow",
    "downarrow", "uparrow", "to", "not", "mid", "vert", "lvert", "rvert", "lVert", "rVert",
    "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle", "limits", "nolimits", "substack",
    "varsigma", "odot", "mathop",
    "quad", "qquad", "ldots", "cdots", "dots", "vdots", "ddots", "hspace", "phantom",
})

TEXT_COMMANDS = frozenset({
    "paragraph", "subparagraph", "par", "sep", "vspace", "smallskip", "medskip", "bigskip",
    "toprule", "midrule", "bottomrule", "hline", "cline", "multirow", "multicolumn", "shortstack",
    "resizebox", "linewidth", "textwidth", "columnwidth", "tabcolsep", "FloatBarrier", "color", "textcolor",
    "includegraphics", "captionsetup", "raggedright", "raggedleft", "centering", "minipage", "endminipage",
    "journal", "ead", "corref", "cortext", "fnref", "fntext", "affiliation", "address", "keyword", "keywords",
})
