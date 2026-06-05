import os
import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find all style={{...}}
    matches = list(re.finditer(r'style=\{\{([^}]+)\}\}', content))
    if not matches:
        return

    print(f"Fixing {filepath} - {len(matches)} styles")
    
    styles_dict = {}
    style_idx = 1
    
    new_content = content
    # We must iterate backwards to replace without messing up indices
    for match in reversed(matches):
        inner = match.group(1).strip()
        # Create a unique key
        key = f"s{style_idx}"
        style_idx += 1
        styles_dict[key] = inner
        
        start, end = match.span()
        new_content = new_content[:start] + f"style={{_styles.{key}}}" + new_content[end:]

    styles_str = "\nconst _styles = {\n"
    for k, v in styles_dict.items():
        styles_str += f"  {k}: {{{v}}},\n"
    styles_str += "};\n"

    new_content = new_content + styles_str

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

for root, dirs, files in os.walk('c:/Users/avina/IMS 2.0 CLAUDE COWORK/ims-2.0-railway-1/frontend/src'):
    for file in files:
        if file.endswith('.tsx'):
            fix_file(os.path.join(root, file))
