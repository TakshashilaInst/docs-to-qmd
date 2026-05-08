local stringify = require("pandoc.utils").stringify

function Pandoc(doc)
  local function slugify(name)
    return name:lower():gsub("%s+", "-")
  end

  if doc.meta.author then
    for i, author in ipairs(doc.meta.author) do
      if type(author) == "table" then
        local name = stringify(author)
        doc.meta.author[i] = {
          name = name,
          metadata = {
            ["page-url"] = slugify(name)
          }
        }
      end
    end
  end

  return doc
end
