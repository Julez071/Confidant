"""
Conversation summarizer for Confidant.
Automatically summarizes messages that have rotated out of the context window,
giving the AI a 'Previously...' section for continuity.
"""

import database
import llm_client


def summarize_if_needed(context_window=20):
    """Check if messages have rotated out of the context window and summarize them."""
    
    # Find the oldest message in the current context window
    oldest_context_id = database.get_oldest_context_msg_id(limit=context_window)
    if not oldest_context_id:
        return
    
    # Find what we've already summarized
    last_summarized_id = database.get_last_summarized_msg_id()
    
    # If there are unsummarized messages before the context window
    if last_summarized_id >= oldest_context_id:
        return  # Everything before the window is already summarized
    
    # Get the unsummarized messages
    unsummarized = database.get_messages_in_range(last_summarized_id, oldest_context_id)
    
    if len(unsummarized) < 4:
        return  # Not enough messages to be worth summarizing
    
    # Build a text representation for the summarizer
    conversation_text = ""
    for msg in unsummarized:
        content = msg['content']
        if isinstance(content, list):
            # Multimodal — extract text parts only
            text_parts = [p.get('text', '') for p in content if p.get('type') == 'text']
            has_image = any(p.get('type') == 'image_url' for p in content)
            content = " ".join(text_parts)
            if has_image:
                content += " (shared an image)"
        if msg['role'] == 'system':
            continue  # Skip system messages (tool results, etc.)
        conversation_text += f"{msg['role'].upper()}: {content[:300]}\n"
    
    if not conversation_text.strip():
        return
    
    summary_prompt = """Summarize this conversation segment in 2-3 sentences. 
Focus on: topics discussed, decisions made, emotions expressed, and anything the participants would want to remember.
Be concise but preserve the emotional texture. Write in third person ("They discussed..." / "The Partner shared...").

Conversation:
""" + conversation_text + "\n\nSummary:"
    
    try:
        summary = llm_client.complete(
            [{"role": "user", "content": summary_prompt}],
            temperature=0.2
        )
        
        if summary and len(summary) > 10:
            first_id = unsummarized[0]['id']
            last_id = unsummarized[-1]['id']
            database.add_conversation_summary(summary.strip(), first_id, last_id)
    except Exception:
        pass  # Summarization failure shouldn't crash anything
