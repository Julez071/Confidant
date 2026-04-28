"""
Unified LLM client for Confidant.
Routes requests to local llama.cpp or external providers (OpenAI, Anthropic, Gemini, DeepSeek)
based on config.json settings.
"""

import os
import json
import urllib.request
import urllib.error
import urllib.parse
import database

# Provider definitions with default models
PROVIDERS = {
    "local": {
        "name": "Local (llama.cpp)",
        "models": [],
        "default_model": "",
    },
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"],
        "default_model": "gpt-4o",
    },
    "gemini": {
        "name": "Google Gemini",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        "default_model": "gemini-2.0-flash",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250414"],
        "default_model": "claude-sonnet-4-20250514",
    },
    "deepseek": {
        "name": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
}


def _get_config():
    return database.get_all_config()


def get_provider_info():
    """Return provider definitions for the frontend."""
    return PROVIDERS


def get_current_config():
    """Return the current AI configuration."""
    config = _get_config()
    return {
        "provider": config.get("AI_PROVIDER", "local"),
        "api_key": config.get("AI_API_KEY", ""),
        "model": config.get("AI_MODEL", ""),
    }


# ---------------------------------------------------------------------------
# Message preprocessing — handle images per provider
# ---------------------------------------------------------------------------

def _strip_images(messages):
    """Strip image parts from messages, keeping only text. For providers without vision."""
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            # Extract only text parts
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            text = " ".join(text_parts).strip()
            if text:
                cleaned.append({"role": msg["role"], "content": text + "\n(An image was shared here.)"})
            else:
                cleaned.append({"role": msg["role"], "content": "(An image was shared here.)"})
        else:
            cleaned.append(msg)
    return cleaned


def _convert_images_for_anthropic(messages):
    """Convert OpenAI-style image_url to Anthropic's source format."""
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for part in content:
                if part.get("type") == "text":
                    new_content.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if url.startswith("data:"):
                        # data:image/jpeg;base64,/9j/4AAQ...
                        header, b64data = url.split(",", 1)
                        mime = header.split(":")[1].split(";")[0]
                        new_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": b64data,
                            }
                        })
            cleaned.append({"role": msg["role"], "content": new_content})
        else:
            cleaned.append(msg)
    return cleaned


def _prepare_messages(messages, provider):
    """Preprocess messages based on provider capabilities."""
    if provider == "deepseek":
        return _strip_images(messages)
    elif provider == "anthropic":
        return _convert_images_for_anthropic(messages)
    # OpenAI, Gemini, and Local pass through as-is
    # (Gemini conversion happens inside _stream_gemini / _complete_gemini)
    return messages


# ---------------------------------------------------------------------------
# Streaming generators — yield content strings for SSE
# ---------------------------------------------------------------------------

def _stream_local(messages):
    """Stream from local llama.cpp server."""
    req = urllib.request.Request(
        "http://localhost:8080/v1/chat/completions",
        data=json.dumps({"messages": messages, "stream": True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            line = line.decode('utf-8').strip()
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str == '[DONE]':
                    break
                try:
                    chunk = json.loads(data_str)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except Exception:
                    pass


def _stream_openai_compatible(messages, api_key, model, base_url):
    """Stream from OpenAI-compatible API (OpenAI, DeepSeek)."""
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps({
            "messages": messages,
            "model": model,
            "stream": True,
        }).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            line = line.decode('utf-8').strip()
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str == '[DONE]':
                    break
                try:
                    chunk = json.loads(data_str)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except Exception:
                    pass


def _stream_anthropic(messages, api_key, model):
    """Stream from Anthropic Messages API."""
    # Anthropic requires system prompt separate from messages
    system_text = ""
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            # Concatenate system messages
            content = msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"])
            system_text += ("\n\n" + content if system_text else content)
        else:
            # Ensure content is in the right format
            filtered_messages.append({
                "role": msg["role"],
                "content": msg["content"] if isinstance(msg["content"], str) else msg["content"],
            })

    # Anthropic requires messages to start with a user message
    if filtered_messages and filtered_messages[0]["role"] != "user":
        filtered_messages.insert(0, {"role": "user", "content": "(Continue the conversation.)"})

    # Ensure alternating roles (Anthropic requirement)
    cleaned = []
    for msg in filtered_messages:
        if cleaned and cleaned[-1]["role"] == msg["role"]:
            # Merge consecutive same-role messages
            prev_content = cleaned[-1]["content"]
            new_content = msg["content"]
            if isinstance(prev_content, str) and isinstance(new_content, str):
                cleaned[-1]["content"] = prev_content + "\n\n" + new_content
            else:
                cleaned[-1]["content"] = str(prev_content) + "\n\n" + str(new_content)
        else:
            cleaned.append(msg)
    filtered_messages = cleaned

    body = {
        "model": model,
        "max_tokens": 4096,
        "stream": True,
        "messages": filtered_messages,
    }
    if system_text:
        body["system"] = system_text

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            line = line.decode('utf-8').strip()
            if line.startswith('data: '):
                data_str = line[6:]
                try:
                    chunk = json.loads(data_str)
                    if chunk.get("type") == "content_block_delta":
                        text = chunk.get("delta", {}).get("text", "")
                        if text:
                            yield text
                    elif chunk.get("type") == "message_stop":
                        break
                except Exception:
                    pass


def _stream_gemini(messages, api_key, model):
    """Stream from Google Gemini REST API."""
    # Convert messages to Gemini format
    system_text = ""
    contents = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"])
            system_text += ("\n\n" + content if system_text else content)
        else:
            role = "user" if msg["role"] == "user" else "model"
            content = msg["content"]
            
            parts = []
            if isinstance(content, list):
                # Multimodal content
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": part["text"]})
                    elif part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:"):
                            # Extract mime type and base64 data
                            header, b64data = url.split(",", 1)
                            mime = header.split(":")[1].split(";")[0]
                            parts.append({"inline_data": {"mime_type": mime, "data": b64data}})
            else:
                parts.append({"text": content})
            
            # Gemini requires alternating user/model, merge consecutive same-role
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

    # Ensure first message is from user
    if contents and contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": "(Continue the conversation.)"}]})

    body = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
        }
    }
    if system_text:
        body["system_instruction"] = {"parts": [{"text": system_text}]}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}"
    
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            line = line.decode('utf-8').strip()
            if line.startswith('data: '):
                data_str = line[6:]
                try:
                    chunk = json.loads(data_str)
                    candidates = chunk.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                except Exception:
                    pass


def stream_chat(messages):
    """
    Stream a chat completion. Yields content strings.
    Reads provider config from config.json on each call.
    """
    config = _get_config()
    provider = config.get("AI_PROVIDER", "local")
    api_key = config.get("AI_API_KEY", "")
    model = config.get("AI_MODEL", "")

    # Preprocess messages for provider-specific image handling
    messages = _prepare_messages(messages, provider)

    if provider == "local" or not provider:
        yield from _stream_local(messages)

    elif provider == "openai":
        model = model or "gpt-4o"
        yield from _stream_openai_compatible(
            messages, api_key, model,
            "https://api.openai.com/v1"
        )

    elif provider == "deepseek":
        model = model or "deepseek-chat"
        yield from _stream_openai_compatible(
            messages, api_key, model,
            "https://api.deepseek.com/v1"
        )

    elif provider == "anthropic":
        model = model or "claude-sonnet-4-20250514"
        yield from _stream_anthropic(messages, api_key, model)

    elif provider == "gemini":
        model = model or "gemini-2.0-flash"
        yield from _stream_gemini(messages, api_key, model)

    else:
        raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Non-streaming completion — for memory manager
# ---------------------------------------------------------------------------

def _complete_local(messages, temperature):
    """Non-streaming completion from local llama.cpp."""
    req = urllib.request.Request(
        "http://localhost:8080/v1/chat/completions",
        data=json.dumps({
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())
    return result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()


def _complete_openai_compatible(messages, api_key, model, base_url, temperature):
    """Non-streaming completion from OpenAI-compatible API."""
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps({
            "messages": messages,
            "model": model,
            "stream": False,
            "temperature": temperature,
        }).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())
    return result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()


def _complete_anthropic(messages, api_key, model, temperature):
    """Non-streaming completion from Anthropic."""
    system_text = ""
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"])
            system_text += ("\n\n" + content if system_text else content)
        else:
            filtered_messages.append({
                "role": msg["role"],
                "content": msg["content"] if isinstance(msg["content"], str) else msg["content"],
            })

    if filtered_messages and filtered_messages[0]["role"] != "user":
        filtered_messages.insert(0, {"role": "user", "content": "(Continue the conversation.)"})

    # Ensure alternating roles
    cleaned = []
    for msg in filtered_messages:
        if cleaned and cleaned[-1]["role"] == msg["role"]:
            prev_content = cleaned[-1]["content"]
            new_content = msg["content"]
            if isinstance(prev_content, str) and isinstance(new_content, str):
                cleaned[-1]["content"] = prev_content + "\n\n" + new_content
            else:
                cleaned[-1]["content"] = str(prev_content) + "\n\n" + str(new_content)
        else:
            cleaned.append(msg)
    filtered_messages = cleaned

    body = {
        "model": model,
        "max_tokens": 4096,
        "messages": filtered_messages,
        "temperature": temperature,
    }
    if system_text:
        body["system"] = system_text

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())

    # Anthropic response: {"content": [{"type": "text", "text": "..."}]}
    content_blocks = result.get("content", [])
    texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "\n".join(texts).strip()


def _complete_gemini(messages, api_key, model, temperature):
    """Non-streaming completion from Gemini."""
    system_text = ""
    contents = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"] if isinstance(msg["content"], str) else json.dumps(msg["content"])
            system_text += ("\n\n" + content if system_text else content)
        else:
            role = "user" if msg["role"] == "user" else "model"
            content = msg["content"]
            parts = [{"text": content}] if isinstance(content, str) else [{"text": json.dumps(content)}]
            
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

    if contents and contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": "(Continue the conversation.)"}]})

    body = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    if system_text:
        body["system_instruction"] = {"parts": [{"text": system_text}]}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        result = json.loads(response.read())

    candidates = result.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(p.get("text", "") for p in parts).strip()
    return ""


def complete(messages, temperature=0.1):
    """
    Non-streaming completion. Returns the full response string.
    Used by memory_manager for evaluation.
    """
    config = _get_config()
    provider = config.get("AI_PROVIDER", "local")
    api_key = config.get("AI_API_KEY", "")
    model = config.get("AI_MODEL", "")

    # Preprocess messages for provider-specific image handling
    messages = _prepare_messages(messages, provider)

    # Clamp temperature to avoid API errors
    if provider == "anthropic":
        temperature = min(temperature, 1.0)
    else:
        temperature = min(temperature, 2.0)

    if provider == "local" or not provider:
        return _complete_local(messages, temperature)

    elif provider == "openai":
        model = model or "gpt-4o"
        return _complete_openai_compatible(
            messages, api_key, model,
            "https://api.openai.com/v1", temperature
        )

    elif provider == "deepseek":
        model = model or "deepseek-chat"
        return _complete_openai_compatible(
            messages, api_key, model,
            "https://api.deepseek.com/v1", temperature
        )

    elif provider == "anthropic":
        model = model or "claude-sonnet-4-20250514"
        return _complete_anthropic(messages, api_key, model, temperature)

    elif provider == "gemini":
        model = model or "gemini-2.0-flash"
        return _complete_gemini(messages, api_key, model, temperature)

    else:
        raise ValueError(f"Unknown provider: {provider}")
