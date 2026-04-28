import json
import database
import llm_client

def get_file_content(name):
    """Read a file from the database by name."""
    return database.get_file(name)

def write_file_content(name, content):
    """Write a file to the database by name."""
    database.set_file(name, content)

def _should_evaluate(recent_history):
    """Fast triage: is this conversation worth a full memory evaluation?
    Uses a tiny prompt with minimal context to avoid wasting tokens on trivial messages."""
    
    # Extract just the last 3 messages as plain text
    snippet = ""
    for msg in recent_history[-3:]:
        content = msg.get('content', '')
        if isinstance(content, list):
            content = " ".join(p.get('text', '') for p in content if p.get('type') == 'text')
        if isinstance(content, str):
            snippet += f"{msg.get('role', 'unknown')}: {content[:200]}\n"
    
    if not snippet.strip():
        return False
    
    triage_prompt = f"""Read the recent exchange below and answer ONLY "yes" or "no":
Does this exchange contain ANY of the following?
- New personal information about the user (name, preferences, life events, people they know)
- A meaningful emotional moment or profound insight
- A change in what they're working on or interested in
- A genuinely beautiful, striking, or poetic quote
- An image the user shared with clear emotional or aesthetic significance

Exchange:
{snippet}
Answer (yes/no):"""
    
    try:
        result = llm_client.complete(
            [{"role": "user", "content": triage_prompt}],
            temperature=0.0
        )
        return "yes" in result.lower()
    except Exception:
        return True  # If triage fails, err on the side of evaluating

def evaluate_memory(history):
    # history is a list of {"role": ..., "content": ...}
    
    # We only care about the last few turns for memory updates to avoid huge context,
    # but let's pass a decent chunk. Say, last 6 messages.
    recent_history = history[-6:]
    
    # Fast triage: skip the expensive full evaluation for trivial messages
    if not _should_evaluate(recent_history):
        return
    
    # Extract just the "Your Files" section from system_prompt.md to avoid conflicting instructions
    system_prompt_content = get_file_content("system_prompt.md")
    files_section = ""
    if "## Your Files" in system_prompt_content:
        # Get everything between "## Your Files" and the next "---"
        files_section = system_prompt_content.split("## Your Files")[1].split("---")[0].strip()

    # Read current files (character.md is self-managed by the AI, not by the memory manager)
    files = {
        "partner.md": get_file_content("partner.md"),
        "significant_others.md": get_file_content("significant_others.md"),
        "context.md": get_file_content("context.md"),
        "instructions.md": get_file_content("instructions.md")
    }
    
    system_prompt = f"""You are the Memory Manager for an AI companion named Confidant.
Your job is to read the recent conversation between Confidant (assistant) and the Partner (user), and decide if any of Confidant's foundational markdown files need to be updated.

IMPORTANT: You do NOT manage character.md. That file is self-managed by Confidant during conversations. You only manage the files listed below.

Here are the rules for when to update each file:
- **partner.md**: Update this when you learn something worth remembering — biographical facts, important preferences, or things the Partner has shared that feel significant.
- **significant_others.md**: Update this when the Partner mentions someone new or shares a development about someone known. Map their relationships carefully.
- **context.md**: Update this frequently as projects evolve, questions open and close, and the Partner's immediate situation changes.
- **instructions.md**: Update this when the Partner explicitly states a preference or rule for how Confidant should interact with them.

To understand exactly what belongs in each file and why they exist, carefully read these definitions from the system prompt:
<file_definitions>
{files_section}
</file_definitions>

Here are the current contents of the files you can modify:
<partner.md>
{files['partner.md']}
</partner.md>

<significant_others.md>
{files['significant_others.md']}
</significant_others.md>

<context.md>
{files['context.md']}
</context.md>

<instructions.md>
{files['instructions.md']}
</instructions.md>

Instructions for updating:
1. ONLY update character.md, partner.md, significant_others.md, context.md, or instructions.md. NEVER modify system_prompt.md.
2. Refer to the <file_definitions> above for the precise definitions of what goes into each file.
3. Rely on your understanding of human relationships and the overarching file definitions to decide when an update is necessary. If a detail feels significant to the Partner's life, identity, ongoing projects, or social world, capture it organically.
4. When you do update, output FULL, completely rewritten valid markdown for the changed files. Do not output partial snippets. Keep the existing structure.

Instructions for the Memory Vault:
In addition to profile files, you are the Curator of the Memory Vault. The Vault is a sacred, highly curated archive — NOT a log. Think of it as a gallery wall where only the most striking pieces belong. Creating a card should be RARE.

STRICT CURATION RULES:
- Most conversations should produce NO vault cards. Default to NO_UPDATES.
- Only create a card if the moment would genuinely stand on its own as something beautiful, profound, or deeply meaningful to the relationship.
- NEVER create cards for: casual exchanges, test messages, greetings, routine updates, technical discussions, or anything ordinary.
- NEVER create a vault card (summary or quote) about Confidant discussing its dreams or subconscious. The dreaming system already creates its own Vault Cards automatically.
- Ask yourself: "Would someone read this card in a year and feel something?" If not, do not create it.

Types of Vault Cards (use sparingly):
- "quote": ONLY for truly striking, original, or poetic lines. The title MUST be empty (""). The content is ONLY the quoted text in quotation marks. Nothing else.
- "summary": ONLY when a genuinely deep, emotionally significant conversation has naturally concluded. The title MUST be a short, evocative heading (2-5 words). The content is the summary text.
- "image": ONLY if the Partner shared a picture ([Image ID: ...]) AND the conversation makes clear it holds real emotional or aesthetic significance.

OUTPUT FORMAT:
If NO updates to files and NO new vault cards are needed, output exactly: NO_UPDATES
If updates or vault cards ARE needed, output a JSON object containing the files that changed AND/OR the "vault_cards" array.
Examples:
{{
  "vault_cards": [{{ "type": "quote", "title": "", "content": "\\"The city is a map of missed opportunities.\\"" }}]
}}
{{
  "vault_cards": [{{ "type": "summary", "title": "Building a Shelter", "content": "A conversation about creating spaces that feel like home." }}]
}}
"""

    # Inject recent vault cards so the LLM can avoid duplicates
    import database
    recent_cards = database.get_vault_cards()[:5]  # Already sorted by id DESC
    if recent_cards:
        system_prompt += "\n\nRecent Memory Vault cards (DO NOT create duplicates or near-duplicates of these):\n"
        for card in recent_cards:
            if card['type'] != 'image':
                label = f"[{card['type'].upper()}]"
                if card['title']:
                    label += f" \"{card['title']}\""
                system_prompt += f"- {label}: {card['content'][:120]}\n"

    # Check if current provider supports images
    config = llm_client._get_config()
    provider = config.get("AI_PROVIDER", "local")
    supports_images = provider not in ("deepseek",)

    prompt = "Recent Conversation:\n"
    for msg in recent_history:
        content = msg['content']
        msg_id = msg.get('id', 'Unknown')
        if isinstance(content, list):
            text_parts = [part['text'] for part in content if part.get('type') == 'text']
            if supports_images:
                content = " ".join(text_parts) + f" [Image ID: {msg_id}]"
            else:
                content = " ".join(text_parts) + " (An image was shared but this model cannot view it.)"
        prompt += f"[{msg['role'].upper()}]: {content}\n"
    
    if not supports_images:
        prompt += "\nNOTE: The current AI model does not support image viewing. Do NOT create any image vault cards.\n"
    
    prompt += "\nOutput your JSON (or NO_UPDATES) below:"

    try:
        content = llm_client.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        
        if "NO_UPDATES" in content and "{" not in content:
            return
            
        # Try to parse JSON from the response. It might be wrapped in ```json ... ```
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        try:
            updates = json.loads(content)
            
            # Handle vault cards
            vault_cards = updates.get("vault_cards", [])
            for card in vault_cards:
                if card.get("type") == "image":
                    # For images, we just save the image ID, but wait, the content should be the actual base64 or URL
                    # so the frontend can render it. The ID refers to the DB row.
                    # We will store the Image ID as a special string in the database e.g., "IMAGE:45"
                    # and the frontend can fetch the image from the DB using a new endpoint if needed, 
                    # or the backend can inject the base64 here. Let's just store "IMAGE:45"
                    image_id_str = card.get("content", "")
                    if "[Image ID: " in image_id_str:
                        img_id = image_id_str.split("[Image ID: ")[1].split("]")[0]
                        database.add_vault_card("image", f"IMAGE:{img_id}", card.get("title", ""))
                else:
                    database.add_vault_card(card.get("type", "insight"), card.get("content", ""), card.get("title", ""))
            
            # Handle file updates
            for filename, new_text in updates.items():
                if filename in files and new_text and isinstance(new_text, str):
                    write_file_content(filename, new_text)
                    # Silently updated in background
        except json.JSONDecodeError:
            # Maybe the model failed to output valid JSON. Silently ignore or log.
            pass
            
    except Exception as e:
        # Ignore errors in background thread to not crash the main app
        pass
