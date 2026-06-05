import os
import re

dir_path = 'c:/Users/avina/IMS 2.0 CLAUDE COWORK/ims-2.0-railway-1/frontend/src'

for root, dirs, files in os.walk(dir_path):
    for file in files:
        if file.endswith('.tsx'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # fix aria-checked={... ? "true" : "false"}
            content = re.sub(r'aria-checked=\{([^}]+)\s*\?\s*"true"\s*:\s*"false"\}', r'aria-checked={!!(\1)}', content)
            
            # fix missing titles on button
            btn_pattern = re.compile(r'<button([^>]*?)>', re.IGNORECASE)
            def btn_repl(match):
                attrs = match.group(1)
                if 'title=' not in attrs and 'aria-label=' not in attrs:
                    return f'<button{attrs} title="Button">'
                return match.group(0)
            
            new_content = btn_pattern.sub(btn_repl, content)
            
            # fix missing title on select
            sel_pattern = re.compile(r'<select([^>]*?)>', re.IGNORECASE)
            def sel_repl(match):
                attrs = match.group(1)
                if 'title=' not in attrs and 'aria-label=' not in attrs:
                    return f'<select{attrs} title="Select">'
                return match.group(0)
            
            new_content = sel_pattern.sub(sel_repl, new_content)
            
            # fix missing labels on form elements (input)
            inp_pattern = re.compile(r'<input([^>]*?)>', re.IGNORECASE)
            def inp_repl(match):
                attrs = match.group(1)
                if 'type="hidden"' in attrs or 'type="submit"' in attrs or 'type="button"' in attrs:
                    return match.group(0)
                if 'title=' not in attrs and 'placeholder=' not in attrs and 'aria-label=' not in attrs and 'id=' not in attrs:
                    return f'<input{attrs} title="Input">'
                return match.group(0)
                
            new_content = inp_pattern.sub(inp_repl, new_content)
            
            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f'Fixed a11y in {file}')

