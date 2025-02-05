import i18n

def tt (message, locale ): #text translator
    if locale in message:
        return message[locale] 
    else: 
        return message[i18n.get('fallback')]    
