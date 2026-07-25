# Copyright (C) 2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Markdown and MathML rendering helpers for text recognition output."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
import logging
import re
from typing import override
from xml.etree import ElementTree

import nh3
from markdown import Markdown, markdown
from markdown.extensions import Extension
from markdown.postprocessors import Postprocessor


_MARKDOWN_EXTENSIONS = [
	"markdown.extensions.extra",
	"markdown.extensions.nl2br",
]

_MARKDOWN_FENCE_INFO_STRINGS = {"markdown", "md", "mdown", "mkdn", "gfm"}
_TEXT_FENCE_INFO_STRINGS = {"", "text", "plain", "plaintext"}

_MATH_DELIMITERS = (
	("$$", "$$", "block"),
	(r"\[", r"\]", "block"),
	(r"\(", r"\)", "inline"),
	("$", "$", "inline"),
)
_LITERAL_NUMERIC_CHARACTER_REFERENCE_PATTERN = re.compile(
	r"(?:(?:&(?:amp|AMP)|&#0*38|&#[xX]0*26);?)#(?:[xX][0-9A-Fa-f]+|\d+);?",
)
_NUMERIC_CHARACTER_REFERENCE_PATTERN = re.compile(r"&#(?:[xX][0-9A-Fa-f]+|\d+);")
_PRIVATE_USE_MARKER_START = 0xF0000
_PRIVATE_USE_MARKER_END = 0xFFFFD
_MATH_TEXT_ONLY_HTML_TAGS = {
	"iframe",
	"noembed",
	"noframes",
	"noscript",
	"script",
	"style",
	"textarea",
	"title",
	"xmp",
}
_MATH_EXCLUDED_HTML_TAGS = _MATH_TEXT_ONLY_HTML_TAGS | {"code", "kbd", "math", "pre", "samp"}

log = logging.getLogger(__name__)

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


def _getMarkdownExtensions() -> list[str | Extension]:
	extensions: list[str | Extension] = list(_MARKDOWN_EXTENSIONS)
	try:
		from l2m4m import LaTeX2MathMLExtension
		from latex2mathml import converter
	except ImportError:
		return extensions

	def convertFormula(formula: str, display: str) -> str:
		protectedFormula, protectedReferences = _protectLiteralNumericCharacterReferences(formula)
		mathElement = converter.convert_to_element(unescape(protectedFormula), display=display)
		_decodeMathMlNumericCharacterReferences(mathElement, protectedReferences)
		return ElementTree.tostring(mathElement, encoding="unicode")

	extensions.extend((LaTeX2MathMLExtension(), _RawHtmlMathExtension(convertFormula)))
	return extensions


def _protectLiteralNumericCharacterReferences(formula: str) -> tuple[str, dict[str, str]]:
	protectedReferences: dict[str, str] = {}
	markerCodePoint = _PRIVATE_USE_MARKER_START
	unescapedFormula = unescape(formula)

	def replaceReference(match: re.Match[str]) -> str:
		nonlocal markerCodePoint
		while markerCodePoint <= _PRIVATE_USE_MARKER_END:
			marker = chr(markerCodePoint)
			markerCodePoint += 1
			if marker not in unescapedFormula and marker not in protectedReferences:
				protectedReferences[marker] = match.group(0)
				return marker
		raise ValueError("Too many literal numeric character references in formula")

	return _LITERAL_NUMERIC_CHARACTER_REFERENCE_PATTERN.sub(replaceReference, formula), protectedReferences


def _decodeMathMlNumericCharacterReferences(
	mathElement: ElementTree.Element,
	protectedReferences: dict[str, str],
) -> None:
	referencePattern = re.compile(
		"|".join((*map(re.escape, protectedReferences), _NUMERIC_CHARACTER_REFERENCE_PATTERN.pattern)),
	)

	def decode(value: str) -> str:
		def replaceReference(match: re.Match[str]) -> str:
			reference = match.group(0)
			return unescape(protectedReferences.get(reference, reference))

		return referencePattern.sub(replaceReference, value)

	for element in mathElement.iter():
		if element.text:
			element.text = decode(element.text)
		if element.tail:
			element.tail = decode(element.tail)
		for name, value in tuple(element.attrib.items()):
			element.set(name, decode(value))


def _isEscaped(text: str, position: int) -> bool:
	backslashCount = 0
	position -= 1
	while position >= 0 and text[position] == "\\":
		backslashCount += 1
		position -= 1
	return backslashCount % 2 == 1


def _matchMathOpener(text: str, position: int) -> tuple[str, str, str] | None:
	for opener, closer, display in _MATH_DELIMITERS:
		if not text.startswith(opener, position) or _isEscaped(text, position):
			continue
		return opener, closer, display
	return None


def _findMathCloser(
	text: str,
	position: int,
	closer: str,
) -> tuple[int | None, int]:
	while position < len(text):
		if text.startswith(closer, position) and not _isEscaped(text, position):
			return position, position + len(closer)
		if _matchMathOpener(text, position) is not None:
			return None, position
		position += 1
	return None, position


def _convertMathText(text: str, convertFormula: Callable[[str, str], str]) -> str:
	"""Convert supported LaTeX delimiters with a single forward scan."""
	output: list[str] = []
	unchangedStart = 0
	position = 0
	while position < len(text):
		delimiter = _matchMathOpener(text, position)
		if delimiter is None:
			position += 1
			continue
		opener, closer, display = delimiter
		formulaStart = position + len(opener)
		formulaEnd, resumePosition = _findMathCloser(text, formulaStart, closer)
		if formulaEnd is None:
			if resumePosition >= len(text):
				break
			position = resumePosition
			continue
		matchEnd = formulaEnd + len(closer)
		formula = text[formulaStart:formulaEnd]
		if not formula:
			position = matchEnd
			continue
		# ponytail: `$` is ambiguous; broader currency detection needs an explicit syntax signal.
		if opener == "$" and formula[0].isdigit() and matchEnd < len(text) and text[matchEnd].isdigit():
			position = matchEnd
			continue
		try:
			mathMl = convertFormula(formula, display)
		except Exception:
			log.warning("Unable to convert LaTeX formula in raw HTML.", exc_info=True)
			position = matchEnd
			continue
		output.extend((text[unchangedStart:position], mathMl))
		unchangedStart = matchEnd
		position = matchEnd
	output.append(text[unchangedStart:])
	return "".join(output)


class _RawHtmlMathParser(HTMLParser):
	"""Convert formulas in raw HTML text while preserving its original markup."""

	def __init__(self, convertFormula: Callable[[str, str], str]) -> None:
		super().__init__(convert_charrefs=False)
		self._convertFormula = convertFormula
		self._excludedTags: list[str] = []
		self._htmlParts: list[str] = []
		self._sourceLines: list[str] = []
		self._textParts: list[str] = []

	def convert(self, htmlText: str) -> str:
		self._sourceLines = htmlText.split("\n")
		self.feed(htmlText)
		self.close()
		self._flushText()
		return "".join(self._htmlParts)

	def _flushText(self) -> None:
		if not self._textParts:
			return
		text = "".join(self._textParts)
		self._textParts.clear()
		self._htmlParts.append(text if self._excludedTags else _convertMathText(text, self._convertFormula))

	@override
	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		self._flushText()
		self._htmlParts.append(self.get_starttag_text() or f"<{tag}>")
		if tag in _MATH_EXCLUDED_HTML_TAGS:
			self._excludedTags.append(tag)

	@override
	def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		self._flushText()
		self._htmlParts.append(self.get_starttag_text() or f"<{tag} />")

	@override
	def handle_endtag(self, tag: str) -> None:
		self._flushText()
		self._htmlParts.append(f"</{tag}>")
		if tag in self._excludedTags:
			tagPosition = len(self._excludedTags) - 1 - self._excludedTags[::-1].index(tag)
			del self._excludedTags[tagPosition:]

	@override
	def handle_data(self, data: str) -> None:
		self._textParts.append(data)

	def _appendCharacterReference(self, reference: str) -> None:
		lineNumber, offset = self.getpos()
		if self._sourceLines[lineNumber - 1].startswith(";", offset + len(reference)):
			reference += ";"
		self._textParts.append(reference)

	@override
	def handle_entityref(self, name: str) -> None:
		self._appendCharacterReference(f"&{name}")

	@override
	def handle_charref(self, name: str) -> None:
		self._appendCharacterReference(f"&#{name}")

	@override
	def handle_comment(self, data: str) -> None:
		self._flushText()
		self._htmlParts.append(f"<!--{data}-->")

	@override
	def handle_decl(self, decl: str) -> None:
		self._flushText()
		self._htmlParts.append(f"<!{decl}>")

	@override
	def handle_pi(self, data: str) -> None:
		self._flushText()
		self._htmlParts.append(f"<?{data}>")

	@override
	def unknown_decl(self, data: str) -> None:
		self._flushText()
		self._htmlParts.append(f"<![{data}]]>")


def _convertMathInRawHtml(htmlText: str, convertFormula: Callable[[str, str], str]) -> str:
	if not any(delimiter[0] in htmlText for delimiter in _MATH_DELIMITERS):
		return htmlText
	try:
		return _RawHtmlMathParser(convertFormula).convert(htmlText)
	except Exception:
		log.warning("Unable to convert LaTeX formulas in raw HTML.", exc_info=True)
		return htmlText


class _RawHtmlMathPostprocessor(Postprocessor):
	def __init__(self, md: Markdown, convertFormula: Callable[[str, str], str]) -> None:
		super().__init__(md)
		self._markdown = md
		self._convertFormula = convertFormula

	@override
	def run(self, text: str) -> str:
		for position, htmlBlock in enumerate(self._markdown.htmlStash.rawHtmlBlocks):
			self._markdown.htmlStash.rawHtmlBlocks[position] = _convertMathInRawHtml(
				htmlBlock,
				self._convertFormula,
			)
		return text


class _RawHtmlMathExtension(Extension):
	def __init__(self, convertFormula: Callable[[str, str], str]) -> None:
		super().__init__()
		self._convertFormula = convertFormula

	@override
	def extendMarkdown(self, md: Markdown) -> None:
		for tag in _MATH_TEXT_ONLY_HTML_TAGS:
			if tag not in md.block_level_elements:
				md.block_level_elements.append(tag)
		md.postprocessors.register(
			_RawHtmlMathPostprocessor(md, self._convertFormula),
			"rawHtmlMath",
			35,
		)


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
