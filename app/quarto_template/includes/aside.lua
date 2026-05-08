-- aside.lua
-- Converts :::aside divs to \aside{} (short) or \begin{inlinenote} (long) in PDF.
-- Suppresses .aside-btn divs in PDF (they are HTML-only download buttons).

local utils = require("pandoc.utils")

function Div(el)
  if FORMAT:match("latex") and el.classes:includes("aside") then

    -- Suppress HTML-only download button divs
    if el.classes:includes("aside-btn") then
      return {}
    end

    -- Measure plain text length to decide aside vs inlinenote
    local plain_text = utils.stringify(el)

    -- Convert the div's content to LaTeX for embedding in the command argument.
    -- Using pandoc.write() ensures correct escaping and inline LaTeX output,
    -- avoiding the \par-inside-argument error that the old split-block approach caused.
    local content_latex = pandoc.write(pandoc.Pandoc(el.content), "latex")

    if #plain_text > 400 then
      return pandoc.RawBlock("latex",
        "\\begin{inlinenote}\n" .. content_latex .. "\n\\end{inlinenote}")
    end

    return pandoc.RawBlock("latex", "\\aside{" .. content_latex .. "}")
  end

  return el
end
