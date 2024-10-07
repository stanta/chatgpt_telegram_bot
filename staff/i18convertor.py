import ast
import os
import re
import json
from transliterate import translit, get_available_language_codes

# Директория с Python файлами
source_dir = '/512-2/memes/chatgpt_telegram_bot/bot'
# Директория для сохранения локализационных файлов
locales_dir = 'locales'
# Язык локализации
locale = 'en'

if not os.path.exists(locales_dir):
    os.makedirs(locales_dir)

# Файл локализации
locale_file_path = os.path.join(locales_dir, f'{locale}.json')

# Функция для поиска строк в AST дереве
def find_strings(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Str) :
            if child.s.find(' ') >=0 : 
              yield child.s
        yield from find_strings(child)

# Функция для транслитерации и преобразования в snake_case
def transliterate_to_snake_case(text):
    transliterated = translit(text[:8], 'ru', reversed=True)
    snake_case = re.sub(r'\W+', '_', transliterated).lower()
    return snake_case

# Функция для замены строк на переменные i18n в Python файле
def replace_strings_in_file(file_path, strings):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    translations = {}
    for original_string in strings:
        # Генерация ключа для i18n с использованием транслитерации и snake_case
        key_base = transliterate_to_snake_case(original_string)
        key = f'i18n_{key_base}'
        
        # Удостоверимся, что ключ уникален
        counter = 1
        unique_key = key
        while unique_key in translations:
            unique_key = f'{key}_{counter}'
            counter += 1
        
        # Замена строки на переменную i18n
        content = re.sub(re.escape(original_string), f'_{unique_key}', content)
        translations[unique_key] = original_string
    
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content)
    
    return translations

# Основной скрипт
all_strings = {}
for root, _, files in os.walk(source_dir):
    for file in files:
        if file.endswith('.py'):
            file_path = os.path.join(root, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=file_path)
            strings = list(find_strings(tree))
            if strings:
                updated_strings = replace_strings_in_file(file_path, strings)
                all_strings.update(updated_strings)

# Сохранение локализационного файла
with open(locale_file_path, 'w', encoding='utf-8') as locale_file:
    json.dump(all_strings, locale_file, ensure_ascii=False, indent=4)

print(f'Локализационные файлы сохранены в {locale_file_path}')