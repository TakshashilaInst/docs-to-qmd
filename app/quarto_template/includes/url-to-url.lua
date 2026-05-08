-- url-to-url.lua
function Link(el)
    -- Replace any link with \url{target} ignoring the text
    return pandoc.RawInline('latex', '\\url{' .. el.target .. '}')
end

function Str(el)
    -- Detect plain URLs and convert them to \url{}
    local url_pattern = '^https?://[%w-_%.%?%.:/%+=&]+$'
    if el.text:match(url_pattern) then
        return pandoc.RawInline('latex', '\\url{' .. el.text .. '}')
    else
        return el
    end
end
