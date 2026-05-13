// Takshashila Institution — Typst document template
// Body font: Inter  |  Header/serif font: TeX Gyre Pagella
// Primary colour: dark maroon #610D3D

#let primary = rgb(97, 13, 61)

// ── Aside / sidenote ────────────────────────────────────────────────────────
// Rendered as an inline callout block so it NEVER overlaps body text.
// A thin left border and tinted background make it visually distinct.
#let aside(body) = block(
  width: 100%,
  above: 1em,
  below: 1em,
  fill: rgb(249, 238, 245),
  stroke: (left: 2.5pt + primary),
  inset: (left: 10pt, right: 8pt, top: 7pt, bottom: 7pt),
  radius: (right: 3pt),
)[
  #set text(fill: primary, size: 8.5pt, font: "TeX Gyre Pagella")
  #set par(spacing: 0.55em, leading: 0.6em)
  #body
]

// ── Endnotes ────────────────────────────────────────────────────────────────
// Quarto converts [^N] / [^N]: text  →  #footnote[text] in Typst.
// We intercept every #footnote call: suppress the page-bottom rendering,
// collect the body in state, and emit a superscript reference number instead.
// render_endnotes() is called at the end of the body to print the full list.

#let _endnotes    = state("_endnotes",    ())
#let _endnote_ctr = counter("_endnote_ctr")

#let render_endnotes() = context {
  let notes = _endnotes.final()
  if notes.len() == 0 { return }
  v(2.5em, weak: true)
  block(width: 100%)[
    #text(size: 12pt, weight: "bold", fill: primary)[ENDNOTES]
    #v(0.35em)
    #line(length: 100%, stroke: 0.5pt + primary)
    #v(0.6em)
    #for (i, note) in notes.enumerate() {
      block(above: 0pt, below: 0.55em)[
        #set text(size: 8.5pt, font: "TeX Gyre Pagella")
        #grid(
          columns: (1.6em, 1fr),
          column-gutter: 0.25em,
          align(right + top)[#text(weight: "bold")[#(i + 1).]],
          note,
        )
      ]
    }
  ]
}

// ── Main template function ──────────────────────────────────────────────────
// Usage:
//   #show: takshashila-doc.with(title: [...], authors: ("A", "B"), ...)
#let takshashila-doc(
  title: [],
  subtitle: [],
  authors: (),
  date: "",
  tldr: [],
  doctype: "",
  docversion: "",
  body,
) = {

  // ── Title page ─────────────────────────────────────────────────────────
  set page(
    paper: "a4",
    margin: (left: 25.4mm, right: 38.1mm, top: 38.1mm, bottom: 38.1mm),
    fill: primary,
    header: none,
    footer: none,
    numbering: none,
  )
  set text(fill: white, font: "TeX Gyre Pagella", size: 10pt)
  set par(spacing: 0.9em, leading: 0.75em, first-line-indent: 0pt)

  // Logo (optional — renderer passes --input has-logo=true only when file exists)
  if sys.inputs.at("has-logo", default: "false") == "true" {
    image("assets/main-logo-dark.png", width: 60mm)
  }
  v(10mm)

  // Title
  text(size: 36pt, weight: "bold")[#title]
  v(6pt)

  // Subtitle
  if type(subtitle) == str {
    if subtitle != "" {
      text(size: 24pt, weight: "bold")[#subtitle]
      v(14pt)
    } else { v(6pt) }
  } else {
    text(size: 24pt, weight: "bold")[#subtitle]
    v(14pt)
  }

  // Authors, doctype, docversion
  if authors.len() > 0 {
    text(size: 14pt)[#authors.join(", ")]
    linebreak()
  }
  if doctype != "" {
    text(size: 12pt)[#doctype]
    linebreak()
  }
  if docversion != "" {
    text(size: 12pt)[#docversion]
  }
  v(32pt)

  // TL;DR
  if type(tldr) == str {
    if tldr != "" {
      text(size: 12pt)[#tldr]
      v(32pt)
    }
  } else {
    text(size: 12pt)[#tldr]
    v(32pt)
  }

  // Recommended citation
  let author-str = authors.join(", ")
  text(size: 10pt, style: "italic")[Recommended Citation: ]
  text(size: 10pt)[#author-str, "#title", #if doctype != "" [#doctype, ]#if docversion != "" [#docversion, ]The Takshashila Institution]
  v(8pt)
  text(size: 12pt)[© The Takshashila Institution, 2025]


  // ── Body pages ─────────────────────────────────────────────────────────
  set page(
    paper: "a4",
    margin: (left: 25.4mm, right: 76.2mm, top: 38.1mm, bottom: 38.1mm),
    fill: white,
    numbering: "1",
    header: {
      set text(fill: primary, font: "TeX Gyre Pagella", size: 9pt, weight: "bold")
      grid(
        columns: (1fr, 1fr),
        align(left)[#doctype],
        align(right)[#title],
      )
      v(-4pt)
      line(length: 100%, stroke: 0.5pt + primary)
    },
    footer: {
      line(length: 100%, stroke: 0.5pt + primary)
      v(-4pt)
      set text(fill: primary, font: "TeX Gyre Pagella", size: 9pt, weight: "bold")
      grid(
        columns: (1fr, 1fr),
        align(left)[#context counter(page).display()],
        align(right)[TAKSHASHILA INSTITUTION],
      )
    },
  )

  set text(fill: black, font: "Inter", size: 10pt)
  // leading = line spacing within a paragraph; spacing = gap between paragraphs.
  // These values match typical Google Docs 1.15 line spacing + space-after-para.
  set par(spacing: 1.1em, leading: 0.75em, first-line-indent: 0pt)

  // ── Image / figure rules ───────────────────────────────────────────────
  // Global backstop: fit "contain" ensures aspect ratio is ALWAYS preserved —
  // no cropping, no squeezing, ever.
  set image(fit: "contain")

  // Add breathing room around every figure.
  show figure: it => block(width: 100%, breakable: false)[
    #v(1em, weak: true)
    #it
    #v(1em, weak: true)
  ]

  // Caption: smaller, italic, grey.
  show figure.caption: set text(size: 8.5pt, style: "italic", fill: rgb(90, 90, 90))

  show heading.where(level: 1): it => block[
    #v(1em, weak: true)
    #text(size: 13pt, weight: "bold", fill: primary, it.body)
    #v(0.4em, weak: true)
  ]
  show heading.where(level: 2): it => block[
    #v(0.8em, weak: true)
    #text(size: 11.5pt, weight: "bold", it.body)
    #v(0.3em, weak: true)
  ]
  show heading.where(level: 3): it => block[
    #v(0.6em, weak: true)
    #text(size: 10.5pt, weight: "bold", it.body)
    #v(0.2em, weak: true)
  ]
  show heading.where(level: 4): it => block[
    #v(0.5em, weak: true)
    #text(size: 10pt, weight: "bold", style: "italic", it.body)
    #v(0.2em, weak: true)
  ]

  // Links in blue
  show link: it => text(fill: rgb(0, 70, 180), it)

  // Intercept footnotes → collect as endnotes, emit superscript ref instead
  show footnote: it => {
    _endnote_ctr.step()
    _endnotes.update(l => l + (it.body,))
    context {
      set text(fill: primary, size: 7pt)
      super[#_endnote_ctr.display()]
    }
  }

  body

  render_endnotes()


  // ── Back page ───────────────────────────────────────────────────────────
  set page(
    paper: "a4",
    fill: primary,
    header: none,
    footer: none,
    numbering: none,
    margin: (left: 25.4mm, right: 25.4mm, top: 38.1mm, bottom: 38.1mm),
  )
  set text(fill: white, font: "TeX Gyre Pagella", size: 16pt)
  set par(spacing: 0.8em)

  if sys.inputs.at("has-logo", default: "false") == "true" {
    image("assets/main-logo-dark.png", width: 60mm)
  }
  v(20pt)

  [The Takshashila Institution is an independent centre for research and
  education in public policy. It is a non-partisan, non-profit organisation
  that advocates the values of freedom, openness, tolerance, pluralism, and
  responsible citizenship. It seeks to transform India through better public
  policies, bridging the governance gap by developing better public servants,
  civil society leaders, professionals, and informed citizens.

  Takshashila creates change by connecting good people, to good ideas and
  good networks. It produces independent policy research in a number of areas
  of governance, it grooms civic leaders through its online education
  programmes and engages in public discourse through its publications and
  digital media.]

  v(30pt)
  [© The Takshashila Institution, 2025]
}
