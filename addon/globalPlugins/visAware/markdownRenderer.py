# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Markdown and MathML rendering helpers for text recognition output."""

from __future__ import annotations

from copy import deepcopy
import re

import nh3
from markdown import markdown


_MARKDOWN_EXTENSIONS = [
	"markdown.extensions.extra",
	"markdown.extensions.nl2br",
]

_MARKDOWN_FENCE_INFO_STRINGS = {"markdown", "md", "mdown", "mkdn", "gfm"}
_TEXT_FENCE_INFO_STRINGS = {"", "text", "plain", "plaintext"}

_OUTER_FENCED_MARKDOWN_PATTERN = re.compile(
	r"\A[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)\n(?P<body>.*)\n(?P=fence)[ \t]*\Z",
	re.DOTALL,
)
MATHML_TAGS = {
	"abs",
	"and",
	"annotation",
	"annotation-xml",
	"apply",
	"approx",
	"arccos",
	"arccosh",
	"arccot",
	"arccoth",
	"arccsc",
	"arccsch",
	"arcsec",
	"arcsech",
	"arcsin",
	"arcsinh",
	"arctan",
	"arctanh",
	"arg",
	"bind",
	"bvar",
	"card",
	"cartesianproduct",
	"cbytes",
	"ceiling",
	"cerror",
	"ci",
	"cn",
	"codomain",
	"complexes",
	"compose",
	"condition",
	"conjugate",
	"cos",
	"cosh",
	"cot",
	"coth",
	"cs",
	"csc",
	"csch",
	"csymbol",
	"curl",
	"declare",
	"degree",
	"determinant",
	"diff",
	"divergence",
	"divide",
	"domain",
	"domainofapplication",
	"emptyset",
	"eq",
	"equivalent",
	"eulergamma",
	"exists",
	"exp",
	"exponentiale",
	"factorial",
	"factorof",
	"false",
	"floor",
	"forall",
	"gcd",
	"geq",
	"grad",
	"gt",
	"ident",
	"image",
	"imaginary",
	"imaginaryi",
	"in",
	"infinity",
	"int",
	"integers",
	"intersect",
	"lambda",
	"laplacian",
	"lcm",
	"leq",
	"limit",
	"list",
	"ln",
	"log",
	"logbase",
	"lowlimit",
	"lt",
	"maction",
	"math",
	"matrix",
	"matrixrow",
	"max",
	"mean",
	"median",
	"menclose",
	"merror",
	"mfenced",
	"mfrac",
	"mglyph",
	"mi",
	"min",
	"minus",
	"mlabeledtr",
	"mmultiscripts",
	"mn",
	"mo",
	"mode",
	"moment",
	"momentabout",
	"mover",
	"mpadded",
	"mphantom",
	"mprescripts",
	"mroot",
	"mrow",
	"ms",
	"mspace",
	"msqrt",
	"mstyle",
	"msub",
	"msubsup",
	"msup",
	"mtable",
	"mtd",
	"mtext",
	"mtr",
	"munder",
	"munderover",
	"naturalnumbers",
	"neq",
	"none",
	"not",
	"notanumber",
	"notin",
	"notprsubset",
	"notsubset",
	"or",
	"otherwise",
	"outerproduct",
	"partialdiff",
	"piece",
	"piecewise",
	"pi",
	"plus",
	"power",
	"primes",
	"product",
	"prsubset",
	"quotient",
	"rationals",
	"real",
	"reals",
	"rem",
	"root",
	"scalarproduct",
	"sdev",
	"sec",
	"sech",
	"selector",
	"semantics",
	"sep",
	"set",
	"setdiff",
	"sin",
	"sinh",
	"subset",
	"sum",
	"tan",
	"tanh",
	"tendsto",
	"times",
	"transpose",
	"true",
	"union",
	"uplimit",
	"variance",
	"vector",
	"vectorproduct",
	"xor",
}

_MATHML_ATTRIBUTES = {
	"accent",
	"accentunder",
	"actiontype",
	"align",
	"bevelled",
	"charalign",
	"close",
	"columnalign",
	"columnlines",
	"columnspacing",
	"columnspan",
	"crossout",
	"decimalpoint",
	"denomalign",
	"depth",
	"dir",
	"display",
	"displaystyle",
	"encoding",
	"fence",
	"form",
	"frame",
	"framespacing",
	"groupalign",
	"height",
	"indentalign",
	"indentalignfirst",
	"indentalignlast",
	"indentshift",
	"indentshiftfirst",
	"indentshiftlast",
	"indenttarget",
	"largeop",
	"length",
	"linethickness",
	"location",
	"longdivstyle",
	"lquote",
	"lspace",
	"mathbackground",
	"mathcolor",
	"mathsize",
	"mathvariant",
	"maxsize",
	"minlabelspacing",
	"minsize",
	"movablelimits",
	"notation",
	"numalign",
	"open",
	"overflow",
	"position",
	"rowalign",
	"rowlines",
	"rowspacing",
	"rowspan",
	"rquote",
	"rspace",
	"scriptlevel",
	"scriptminsize",
	"scriptsizemultiplier",
	"selection",
	"separator",
	"separators",
	"side",
	"stackalign",
	"stretchy",
	"subscriptshift",
	"superscriptshift",
	"symmetric",
	"voffset",
	"width",
	"xmlns",
	"xref",
}

_URL_ATTRIBUTES = {"href", "src", "definitionurl"}


def _getFenceInfoLanguage(info: str) -> str:
	infoParts = info.strip().lower().split(None, 1)
	return infoParts[0] if infoParts else ""


def _looksLikeMarkdownDocument(text: str) -> bool:
	indicators = 0
	for line in text.splitlines():
		strippedLine = line.strip()
		if not strippedLine:
			continue
		if re.match(r"#{1,6}\s+\S", strippedLine):
			indicators += 1
		elif re.match(r"([-*+]|\d+[.)])\s+\S", strippedLine):
			indicators += 1
		elif re.match(r">\s+\S", strippedLine):
			indicators += 1
		elif strippedLine.startswith("|") and strippedLine.endswith("|"):
			indicators += 1
		elif "**" in strippedLine or "__" in strippedLine:
			indicators += 1
		if indicators >= 2:
			return True
	return False


def _unwrapOuterMarkdownFence(text: str) -> str:
	"""
	Unwrap model responses that put the whole Markdown answer in one code fence.

	Real code blocks inside the Markdown are handled by Python-Markdown.
	"""
	strippedText = text.strip()
	match = _OUTER_FENCED_MARKDOWN_PATTERN.match(strippedText)
	if not match:
		return text
	language = _getFenceInfoLanguage(match.group("info"))
	body = match.group("body")
	if language in _MARKDOWN_FENCE_INFO_STRINGS:
		return body.strip("\n")
	if language in _TEXT_FENCE_INFO_STRINGS and _looksLikeMarkdownDocument(body):
		return body.strip("\n")
	return text


def _getMarkdownExtensions() -> list[object]:
	extensions = list(_MARKDOWN_EXTENSIONS)
	try:
		from l2m4m import LaTeX2MathMLExtension
	except ImportError:
		return extensions
	extensions.append(LaTeX2MathMLExtension())
	return extensions


def renderMarkdownToHtml(text: str) -> str:
	"""
	Render Markdown recognition text to an HTML fragment.

	LaTeX formulas are converted to MathML through the Markdown extension path.
	"""
	if not text:
		return ""
	text = _unwrapOuterMarkdownFence(text)
	try:
		return markdown(text, extensions=_getMarkdownExtensions())
	except Exception:
		return markdown(text, extensions=_MARKDOWN_EXTENSIONS)


def _createAllowedAttributes() -> dict[str, set[str]]:
	allowedAttributes: dict[str, set[str]] = deepcopy(nh3.ALLOWED_ATTRIBUTES)
	for tag in ("div", "span", "p", "pre", "code", "td", "th"):
		allowedAttributes.setdefault(tag, set()).add("class")
	for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
		allowedAttributes.setdefault(tag, set()).add("id")
	for tag in MATHML_TAGS:
		allowedAttributes[tag] = set(_MATHML_ATTRIBUTES)
	allowedAttributes.setdefault("annotation", set()).add("encoding")
	allowedAttributes.setdefault("annotation-xml", set()).add("encoding")
	return allowedAttributes


def _isSafeUrl(value: str) -> bool:
	lowerValue = value.strip().lower()
	return not lowerValue.startswith(("javascript:", "vbscript:", "data:"))


def _attributeFilter(tag: str, attr: str, value: str) -> str | None:
	attr = attr.lower()
	if attr == "src":
		return None
	if attr in _URL_ATTRIBUTES and not _isSafeUrl(value):
		return None
	if tag in MATHML_TAGS:
		if attr == "display":
			return value if value in {"inline", "block"} else None
		if attr == "xmlns":
			return value if value == "http://www.w3.org/1998/Math/MathML" else None
	return value


ALLOWED_TAGS = (nh3.ALLOWED_TAGS - {"img", "picture", "source"}) | {"tfoot"} | MATHML_TAGS
ALLOWED_ATTRIBUTES = _createAllowedAttributes()


def sanitizeRenderedHtml(htmlText: str) -> str:
	"""Sanitize rendered Markdown while preserving safe MathML structure."""
	return nh3.clean(
		htmlText,
		tags=ALLOWED_TAGS,
		attributes=ALLOWED_ATTRIBUTES,
		attribute_filter=_attributeFilter,
	)


def showMarkdownBrowseableMessage(
	text: str,
	title: str,
	closeButton: bool = False,
	copyButton: bool = False,
) -> None:
	"""Render text and show it in NVDA's browseable message dialog."""
	import ui

	ui.browseableMessage(
		renderMarkdownToHtml(text),
		title=title,
		isHtml=True,
		closeButton=closeButton,
		copyButton=copyButton,
		sanitizeHtmlFunc=sanitizeRenderedHtml,
	)
