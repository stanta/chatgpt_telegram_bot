import i18n

def tt (message, locale ): #text translator
    if locale in message:
        return message[locale] 
    else: 
        return message[i18n.get('fallback')]    

def split_text_into_chunks(text, chunk_size):
    cur_chunk_size = chunk_size
    i = 0
    chunked_text = []
    
    while i < len(text):
        if len(text[i:]) <= chunk_size:
                chunked_text.append(text[i:])
                return chunked_text
        else:      
            # находим индекс пробела в пределах текущего чанка с конца
            space_index = text.rfind(' ', i, i + chunk_size)
            # если пробел найден, обрезаем текст до него
            if space_index != -1:
                chunked_text.append(text[i:space_index])                
                i = space_index + 1
            # если пробел не найден, обрезаем текст до конца чанка
            else:
                chunked_text.append(text[i:i + chunk_size])
                i += chunk_size
