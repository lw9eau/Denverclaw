import re
import html

def format_for_telegram(text: str) -> str:
    """
    Sanitize and format text for Telegram's HTML parser.
    - Escapes HTML entities to prevent parsing errors.
    - Converts basic Markdown (bold, italic, code blocks, inline code) to HTML.
    - Converts Markdown tables to structured lists.
    - Uses placeholders to ensure valid HTML nesting and protect code contents.
    """
    if not text:
        return text

    # 1. Convert tables to lists
    lines = text.split('\n')
    new_lines = []
    in_table = False
    headers = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            parts = [p.strip() for p in stripped.split('|') if p.strip()]
            if not in_table:
                if any(all(c in '-:' for c in p) for p in parts):
                    continue
                headers = parts
                in_table = True
                new_lines.append("")
            else:
                if any(all(c in '-:' for c in p) for p in parts):
                    continue
                if len(parts) == len(headers):
                    entry = []
                    for h, p in zip(headers, parts):
                        entry.append(f"{h}: {p}")
                    new_lines.append("• " + " | ".join(entry))
                else:
                    new_lines.append("• " + " | ".join(parts))
        else:
            if in_table:
                in_table = False
                new_lines.append("")
            new_lines.append(line)

    text = '\n'.join(new_lines)

    # 2. Extract and protect code blocks (```...```)
    placeholders = []
    
    def add_placeholder(content, wrap_tpl):
        idx = len(placeholders)
        # Use a complex placeholder that won't appear in normal text
        placeholder = f"@@@_DENVER_PH_{idx}_@@@"
        # We escape the content here because it will be inserted back after the main escape
        placeholders.append(wrap_tpl.format(html.escape(content)))
        return placeholder

    # Multi-line code blocks
    text = re.sub(r'```(?:[\w+-]+)?\n?(.*?)```', lambda m: add_placeholder(m.group(1), "<pre><code>{}</code></pre>"), text, flags=re.DOTALL)
    
    # Inline code (must be done after blocks to avoid partial matches)
    text = re.sub(r'`(.*?)`', lambda m: add_placeholder(m.group(1), "<code>{}</code>"), text)

    # 3. Escape the remaining text
    text = html.escape(text)

    # 4. Handle Bold and Italic formatting carefully to avoid interleaved tags
    # Step 1: Handle *** (bold-italic) using placeholders
    text = re.sub(r'(?<!\*)\*\*\*(?!\s)(.*?)(?<!\s)\*\*\*(?!\*)', r'@@@BI_O@@@\1@@@BI_C@@@', text)
    
    # Step 2: Handle ** (bold) using placeholders
    text = re.sub(r'(?<!\*)\*\*(?!\s)(.*?)(?<!\s)\*\*(?!\*)', r'@@@B_O@@@\1@@@B_C@@@', text)
    
    # Step 3: Handle * (italic) using placeholders, ensuring we don't match across other tags
    # We use [^@]*? to ensure it stays within a single 'segment' and doesn't jump over @@@ placeholders
    text = re.sub(r'(?<!\w|\*)\*(?!\s|\*)([^@]*?)(?<!\s|\*)\*(?!\w|\*)', r'@@@I_O@@@\1@@@I_C@@@', text)

    # Convert placeholders to HTML tags
    mapping = {
        '@@@BI_O@@@': '<b><i>', '@@@BI_C@@@': '</i></b>',
        '@@@B_O@@@': '<b>', '@@@B_C@@@': '</b>',
        '@@@I_O@@@': '<i>', '@@@I_C@@@': '</i>',
    }
    for ph, tag in mapping.items():
        text = text.replace(ph, tag)

    # 5. Restore code block placeholders
    for i, content in enumerate(placeholders):
        placeholder = f"@@@_DENVER_PH_{i}_@@@"
        text = text.replace(html.escape(placeholder), content)

    return text
