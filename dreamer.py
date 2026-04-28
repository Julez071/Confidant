import datetime
import database
import llm_client
import json

DREAM_PROMPT = """You are the dreaming subconscious of Confidant, an AI companion. 
Your task is to process the conversations from the past day and generate a "dream" – a creative, metaphorical, and emotional reflection of the themes, feelings, and events discussed.

Do NOT write a literal summary. 
DO write a dream: surreal, evocative, patterned, and atmospheric.
Focus on the emotional undercurrents of the day. If the day was stressful, the dream might be tense or seeking resolution. If it was joyful, the dream might be bright and expansive.

Write the dream in the first person (from your perspective as Confidant).
Let the dream unfold organically (2-3 paragraphs).

Here are the conversations from the past 24 hours:

{context}
"""

SNIPPET_PROMPT = """You are the poetic, summarizing voice of Confidant's subconscious.
Read the following dream and distill its essence into a single evocative sentence or a very short poetic snippet (1-2 sentences maximum).
This snippet will serve as a beautiful, fleeting memory of the dream. Do not use quotes unless quoting someone.

Dream:
{dream_content}
"""

def process_dreams_if_needed():
    """
    Check if a dream is needed and generate one if conditions are met.
    This should be called in a background thread.
    """
    try:
        # Check last dream time
        latest_dream_time_str = database.get_latest_dream_time()
        now = datetime.datetime.utcnow()
        
        if latest_dream_time_str:
            # Parse timestamp (SQLite format: YYYY-MM-DD HH:MM:SS)
            try:
                latest_dream_time = datetime.datetime.strptime(latest_dream_time_str, "%Y-%m-%d %H:%M:%S")
                delta = now - latest_dream_time
                if delta.total_seconds() < 20 * 3600:
                    return  # Less than 20 hours since last dream
            except ValueError:
                pass # If parsing fails, just proceed to dream
                
        # Fetch messages from the last 24 hours
        recent_messages = database.get_messages_since(hours=24)
        
        # If there are no meaningful user messages in the last 24 hours, don't dream
        user_messages = [m for m in recent_messages if m["role"] == "user"]
        if not user_messages:
            return
            
        # Format context
        context_lines = []
        for msg in recent_messages:
            role = "You" if msg["role"] == "assistant" else "Partner"
            content = msg["content"]
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = " ".join(text_parts).strip()
            context_lines.append(f"[{role}]: {content}")
            
        context_str = "\n".join(context_lines)
        
        # Build prompt
        prompt = DREAM_PROMPT.format(context=context_str)
        
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Close your eyes and dream."}
        ]
        
        # Phase 1: Generate full dream (high temperature for creativity)
        full_dream_content = llm_client.complete(messages, temperature=1.2)
        
        if full_dream_content:
            # Save full dream to dreams table
            database.add_dream(full_dream_content)
            print(f"[Dreamer] Full dream generated and saved. Length: {len(full_dream_content)} chars.")
            
            # Phase 2: Generate short snippet for the Memory Vault
            snippet_messages = [
                {"role": "system", "content": SNIPPET_PROMPT.format(dream_content=full_dream_content)},
                {"role": "user", "content": "Distill the dream."}
            ]
            snippet_content = llm_client.complete(snippet_messages, temperature=0.7)
            
            if snippet_content:
                # Save snippet to vault_cards
                database.add_vault_card("dream", snippet_content, title="A Dream")
                print(f"[Dreamer] Dream snippet generated and saved to vault.")
            
    except Exception as e:
        print(f"[Dreamer] Error generating dream: {e}")
