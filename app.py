from flask import Flask, send_from_directory, request, Response, jsonify, session
import os
import sys
import json
import re
import urllib.request
import urllib.parse
import gzip
import threading
import memory_manager
import database
import llm_client
import summarizer
import crypto

# When frozen by PyInstaller, use a 'data' subdirectory for user data
# and the _internal directory for bundled assets
if getattr(sys, 'frozen', False):
    DIRECTORY = os.path.join(os.path.dirname(sys.executable), 'data')
    BUNDLE_DIR = sys._MEIPASS  # PyInstaller's internal data directory
else:
    DIRECTORY = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = DIRECTORY

os.makedirs(DIRECTORY, exist_ok=True)

app = Flask(__name__,
            static_folder=os.path.join(BUNDLE_DIR, 'static'),
            static_url_path='/static')

# Attempt keyring unlock on startup (so server can decrypt data)
crypto.try_auto_unlock()

# Set secret key AFTER auto-unlock so it uses the stable derived key
# (if set before unlock, the key is random and changes on every restart)
app.secret_key = crypto.get_flask_secret()


import dreamer

def build_system_prompt():
    """Assemble the full system prompt with tools and current file contents."""
    system_prompt = database.get_file("system_prompt.md")
    
    files_to_inject = ["character.md", "partner.md", "significant_others.md", "context.md", "instructions.md"]
    
    injected_content = "\n\n--- CURRENT FILE CONTENTS ---\n\n"
    for filename in files_to_inject:
        content = database.get_file(filename)
        injected_content += f"<{filename}>\n{content}\n</{filename}>\n\n"
        
    search_instruction = """
--- AUTONOMOUS TOOLS ---
You have access to two autonomous tools. To use a tool, output EXACTLY the XML tag and NOTHING ELSE. The system will pause you, execute the tool, and provide you with the results.

1. WEB SEARCH: If the Partner asks a question about recent events, current information, facts, or something you do not know, search the web:
<search>your search query</search>

2. MEMORY RECALL: If the Partner refers to a past conversation, a shared moment, a quote, something you discussed before, or implies you should remember something that is not in your immediate context, search your memory. This searches both the Memory Vault (curated moments) and your full conversation history:
<recall>your search query</recall>

Memory is imperfect. The recall tool returns search results, not guaranteed perfect recall. When results are partial, ambiguous, or absent:
- Be honest: "I remember something about that, but the details are fuzzy..."
- If you find nothing, say so warmly — do not invent a memory.
- If results are fragmentary, share what you found and acknowledge the gaps.
Your Partner will trust you more for your honesty than for false confidence.

3. CREATE MEMORY: If the Partner explicitly asks you to save, preserve, or create a memory from something — a quote, a moment, an insight, or a picture — you can create a Memory Vault card. Include this tag ANYWHERE in your response (it will be silently removed from what the Partner sees):
<vault_card type="quote" title="optional title">the content to preserve</vault_card>

Valid types:
- "quote" — for specific lines or sayings. Content is the text to preserve.
- "summary" — for a synthesized description of a moment or topic. Content is the summary text.
- "image" — for saving a picture the Partner shared. Content should be a short caption/title for the image. The system will automatically find and attach the most recent image from the conversation.

Only use this when the Partner asks. The Memory Manager handles automatic curation — this tool is for explicit requests only.

IMPORTANT: You may only use ONE of the search/recall tools ONCE per turn. If a tool fails, DO NOT try to use it again. Simply inform the Partner or answer using existing knowledge. The vault_card tag is not a tool — it can be included alongside your normal response.
"""
        
    # Inject conversation summaries for continuity
    summaries = database.get_recent_summaries(limit=3)
    if summaries:
        context_section = "\n\n--- CONVERSATION CONTEXT ---\n"
        context_section += "Previous conversations you may vaguely remember:\n"
        for s in reversed(summaries):  # Oldest first
            context_section += f"- {s['summary']}\n"
    else:
        context_section = ""

    # Inject the latest dream if there is one
    dream = database.get_latest_full_dream()
    if dream:
        context_section += "\n--- RECENT DREAM ---\n"
        context_section += "Last night, while the Partner was away, your subconscious processed the day's experiences into the following dream:\n"
        context_section += f"<latest_dream>\n{dream}\n</latest_dream>\n"
        context_section += "You may refer to this dream if it feels natural and relevant to the conversation, but you are not obligated to mention it.\n"

    return system_prompt + search_instruction + injected_content + context_section


def get_confidant_name():
    """Extract the AI's name from character.md."""
    content = database.get_file("character.md")
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.strip() == "## Name":
            if i + 1 < len(lines):
                name = lines[i+1].strip()
                if name:
                    return name
    return "Confidant"



@app.before_request
def check_lock():
    """Block API calls when the database is locked OR the browser session is not authenticated."""
    allowed = [
        '/', '/api/auth/status', '/api/auth/unlock', '/api/auth/set-passphrase',
        '/api/auth/webauthn/register/options', '/api/auth/webauthn/register/verify',
        '/api/auth/webauthn/auth/options', '/api/auth/webauthn/auth/verify',
    ]
    if request.path in allowed or request.path.startswith('/static/'):
        return None

    # Check 1: Is the encryption layer locked? (no key in memory)
    if crypto.is_locked():
        return jsonify({"error": "locked", "message": "Database is locked"}), 403

    # Check 2: Is this browser session authenticated?
    if crypto.has_passphrase() and not session.get('authenticated'):
        return jsonify({"error": "session", "message": "Session not authenticated"}), 403

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    # Detect if this is a fresh install needing AI provider setup
    needs_setup = False
    needs_onboarding = False
    if not crypto.is_locked():
        provider = database.get_config_value('AI_PROVIDER', 'local')
        api_key = database.get_config_value('AI_API_KEY', '')
        needs_setup = (provider == 'local' and not api_key)
        partner = database.get_file('partner.md')
        needs_onboarding = not partner.strip()
    
    return jsonify({
        "has_passphrase": crypto.has_passphrase(),
        "is_locked": crypto.is_locked(),
        "session_authenticated": bool(session.get('authenticated')),
        "has_webauthn": crypto.has_webauthn(),
        "needs_setup": needs_setup,
        "needs_onboarding": needs_onboarding,
    })

@app.route('/api/auth/unlock', methods=['POST'])
def auth_unlock():
    passphrase = request.json.get('passphrase', '')
    if not passphrase:
        return jsonify({"success": False, "error": "Passphrase required"}), 400
    if crypto.unlock(passphrase):
        session['authenticated'] = True
        threading.Thread(target=dreamer.process_dreams_if_needed, daemon=True).start()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Wrong passphrase"}), 401

@app.route('/api/auth/set-passphrase', methods=['POST'])
def auth_set_passphrase():
    passphrase = request.json.get('passphrase', '')
    if not passphrase or len(passphrase) < 4:
        return jsonify({"success": False, "error": "Passphrase must be at least 4 characters"}), 400
    if crypto.has_passphrase():
        return jsonify({"success": False, "error": "Passphrase already set"}), 400
    if crypto.set_passphrase(passphrase):
        app.secret_key = crypto.get_flask_secret()  # First-time setup: switch to stable key
        session['authenticated'] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Failed to set passphrase"}), 500

@app.route('/api/auth/change-passphrase', methods=['POST'])
def auth_change_passphrase():
    old_passphrase = request.json.get('old_passphrase', '')
    new_passphrase = request.json.get('new_passphrase', '')
    if not old_passphrase or not new_passphrase:
        return jsonify({"success": False, "error": "Both old and new passphrase required"}), 400
    if len(new_passphrase) < 4:
        return jsonify({"success": False, "error": "New passphrase must be at least 4 characters"}), 400
    if crypto.change_passphrase(old_passphrase, new_passphrase):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Current passphrase is incorrect"}), 401

# --- WebAuthn (Biometric) Endpoints ---

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
)

def _get_rp_id():
    """WebAuthn RP ID must be a domain, not an IP. Always use 'localhost'."""
    return "localhost"

def _get_origin():
    """Accept both localhost and 127.0.0.1 origins."""
    return [
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ]

@app.route('/api/auth/webauthn/register/options', methods=['POST'])
def webauthn_register_options():
    """Generate WebAuthn registration options."""
    if not crypto.has_passphrase():
        return jsonify({"error": "Set a passphrase first"}), 400

    rp_id = _get_rp_id()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Confidant",
        user_name="confidant_user",
        user_display_name="Confidant User",
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.DISCOURAGED,
        ),
    )

    # Store challenge in session for verification
    session['webauthn_challenge'] = options.challenge

    return Response(options_to_json(options), content_type='application/json')

@app.route('/api/auth/webauthn/register/verify', methods=['POST'])
def webauthn_register_verify():
    """Verify WebAuthn registration response."""
    challenge = session.get('webauthn_challenge')
    if not challenge:
        return jsonify({"success": False, "error": "No challenge found"}), 400

    try:
        verification = verify_registration_response(
            credential=request.get_json(),
            expected_challenge=challenge,
            expected_rp_id=_get_rp_id(),
            expected_origin=_get_origin(),
            require_user_verification=True,
        )

        # Store credential
        crypto.store_webauthn_credential(
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
        )

        session.pop('webauthn_challenge', None)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/auth/webauthn/auth/options', methods=['POST'])
def webauthn_auth_options():
    """Generate WebAuthn authentication options."""
    cred = crypto.get_webauthn_credential()
    if not cred:
        return jsonify({"error": "No biometric credential registered"}), 400

    rp_id = _get_rp_id()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cred['credential_id'])],
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    session['webauthn_challenge'] = options.challenge

    return Response(options_to_json(options), content_type='application/json')

@app.route('/api/auth/webauthn/auth/verify', methods=['POST'])
def webauthn_auth_verify():
    """Verify WebAuthn authentication response and set session."""
    challenge = session.get('webauthn_challenge')
    if not challenge:
        return jsonify({"success": False, "error": "No challenge found"}), 400

    cred = crypto.get_webauthn_credential()
    if not cred:
        return jsonify({"success": False, "error": "No credential found"}), 400

    try:
        verification = verify_authentication_response(
            credential=request.get_json(),
            expected_challenge=challenge,
            expected_rp_id=_get_rp_id(),
            expected_origin=_get_origin(),
            credential_public_key=cred['public_key'],
            credential_current_sign_count=cred['sign_count'],
            require_user_verification=False,  # WebView2 doesn't set UV flag
        )

        crypto.update_sign_count(verification.new_sign_count)
        session.pop('webauthn_challenge', None)
        
        session['authenticated'] = True
        threading.Thread(target=dreamer.process_dreams_if_needed, daemon=True).start()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/files', methods=['GET'])
def get_files():
    files_to_read = ["character.md", "partner.md", "significant_others.md", "context.md", "instructions.md"]
    data = {}
    for filename in files_to_read:
        data[filename] = database.get_file(filename)
    data["name"] = get_confidant_name()
    return jsonify(data)

def get_config():
    return database.get_all_config()

def save_config(config_data):
    for key, value in config_data.items():
        database.set_config_value(key, value)

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        save_config(request.json)
        return jsonify({"status": "success"})
    return jsonify(get_config())

# AI provider configuration endpoints
@app.route('/api/ai/providers', methods=['GET'])
def get_providers():
    return jsonify(llm_client.get_provider_info())

@app.route('/api/ai/config', methods=['GET', 'POST'])
def handle_ai_config():
    if request.method == 'POST':
        data = request.json
        database.set_config_value('AI_PROVIDER', data.get('provider', 'local'))
        database.set_config_value('AI_API_KEY', data.get('api_key', ''))
        database.set_config_value('AI_MODEL', data.get('model', ''))
        return jsonify({"status": "success"})
    return jsonify(llm_client.get_current_config())

@app.route('/api/history', methods=['GET'])
def get_history():
    # Return non-hidden history for the UI to render
    history = database.get_history(limit=200)
    # Filter out hidden messages (e.g. vault-talk prompts) and system messages
    history = [m for m in history if not m.get('is_hidden') and m.get('role') != 'system']
    return jsonify(history)

@app.route('/api/search_history', methods=['GET'])
def search_history():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    return jsonify(database.search_history(query))

@app.route('/api/vault', methods=['GET'])
def get_vault():
    return jsonify(database.get_vault_cards())

@app.route('/api/vault/image/<int:msg_id>', methods=['GET'])
def get_vault_image(msg_id):
    msg = database.get_message_by_id(msg_id)
    if msg and isinstance(msg['content'], list):
        for part in msg['content']:
            if part.get('type') == 'image_url':
                return jsonify({"image_url": part['image_url']['url']})
    return jsonify({"error": "Image not found"}), 404

@app.route('/api/vault/<int:card_id>', methods=['DELETE'])
def delete_vault_card(card_id):
    database.delete_vault_card(card_id)
    return jsonify({"status": "success"})

@app.route('/api/factory-reset', methods=['POST'])
def factory_reset():
    """
    Permanently wipe all personal data and restore Confidant to first-run state.
    Requires an active authenticated session — cannot be called unauthenticated.
    """
    if not session.get('authenticated'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        # Wipe the database (security table, messages, vault, files, config, dreams)
        database.factory_reset()
        # Clear the in-memory key and remove keyring credential
        crypto.reset_auth()
        # Clear the Flask session
        session.clear()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[Factory Reset] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------------------------------------------------------------------
# Chat helpers — extracted from generate() for clarity and safety
# ---------------------------------------------------------------------------

def handle_web_search(search_query):
    """Execute Brave Search and return results text."""
    config = get_config()
    brave_key = config.get("BRAVE_API_KEY", "").strip()
    
    if not brave_key:
        return "Error: Brave Search API Key is not configured. Inform the Partner that they need to set up the API key in the Settings tab before you can search the web."
    
    try:
        req_brave = urllib.request.Request(
            f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(search_query)}",
            headers={
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip',
                'X-Subscription-Token': brave_key
            }
        )
        with urllib.request.urlopen(req_brave, timeout=10) as response_brave:
            if response_brave.info().get('Content-Encoding') == 'gzip':
                data = json.loads(gzip.decompress(response_brave.read()).decode('utf-8'))
            else:
                data = json.loads(response_brave.read().decode('utf-8'))
                
            results = []
            for item in data.get('web', {}).get('results', [])[:4]:
                results.append(f"{item.get('title')}: {item.get('description')}")
                
            if results:
                return f"Brave Search results for '{search_query}':\n" + "\n".join(results)
            else:
                return f"Brave Search returned no results for '{search_query}'."
    except Exception as e:
        return f"Brave Search API failed: {e}. Inform the Partner of the error and DO NOT try to search again."


def handle_recall(recall_query):
    """Search vault + conversation history, return combined results text."""
    recall_results_text = f"Memory recall results for '{recall_query}':\n"
    has_results = False
    
    # Search the Memory Vault (curated memories)
    vault_results = database.search_vault(recall_query, limit=3)
    if vault_results:
        has_results = True
        recall_results_text += "\n--- MEMORY VAULT (curated moments) ---\n"
        for v in vault_results[::-1]:
            label = f"[{v['type'].upper()}]"
            if v['title']:
                label += f" \"{v['title']}\""
            recall_results_text += f"[{v['timestamp']}] {label}: {v['content']}\n"
    
    # Search conversation history
    conv_results = database.search_history(recall_query, limit=5)
    if conv_results:
        has_results = True
        recall_results_text += "\n--- CONVERSATION HISTORY (raw exchanges) ---\n"
        for r in conv_results[::-1]:
            recall_results_text += f"[{r['timestamp']}] {r['role'].upper()}: {r['content']}\n"
    
    if not has_results:
        return f"No memories or past conversations found matching '{recall_query}'. Be honest with the Partner that you cannot find this memory — do not fabricate one."
    
    recall_results_text += "\nIMPORTANT: These are search results, not perfect recall. If the results are partial or ambiguous, express your uncertainty naturally. Do not present fragmentary results as confident knowledge."
    return recall_results_text


def _handle_character_update(response_content):
    """Safely extract and apply <update_character> tag using regex. Returns cleaned response."""
    pattern = re.compile(r'<update_character>(.*?)</update_character>', re.DOTALL)
    match = pattern.search(response_content)
    
    if not match:
        return response_content
    
    char_content = match.group(1).strip()
    
    # Validation: must look like valid markdown with the Character heading
    if char_content and '# Character' in char_content:
        try:
            database.set_file('character.md', char_content)
        except Exception:
            pass
    
    return pattern.sub('', response_content).strip()


def _handle_vault_cards(response_content, history):
    """Extract and apply <vault_card> tags. Returns cleaned response."""
    vault_pattern = re.compile(r'<vault_card\s+type="(\w+)"(?:\s+title="([^"]*)")?>(.*?)</vault_card>', re.DOTALL)
    vault_matches = vault_pattern.findall(response_content)
    
    if vault_matches:
        # Check if the latest user message contains an image
        latest_image_id = None
        for msg in reversed(history):
            if msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                for part in msg['content']:
                    if part.get('type') == 'image_url':
                        latest_image_id = msg.get('id')
                        break
                break
            elif msg.get('role') == 'user':
                break
        
        for card_type, card_title, card_content in vault_matches:
            if not card_content.strip():
                continue  # Skip empty cards
            try:
                if latest_image_id:
                    title = card_title.strip() or card_content.strip()
                    database.add_vault_card('image', f"IMAGE:{latest_image_id}", title)
                else:
                    database.add_vault_card(card_type.strip(), card_content.strip(), card_title.strip())
            except Exception:
                pass
    
    return vault_pattern.sub('', response_content).strip()


def process_response_tags(response_content, history):
    """Handle all response tags. Returns cleaned response."""
    response_content = _handle_character_update(response_content)
    response_content = _handle_vault_cards(response_content, history)
    return response_content


# ---------------------------------------------------------------------------
# Main chat endpoint
# ---------------------------------------------------------------------------

@app.route('/api/chat', methods=['POST'])
def chat():
    req_data = request.json
    
    # Store the incoming message in the database
    is_hidden = req_data.get('isHidden', False)
    if 'message' in req_data:
        database.add_message('user', req_data['message'], is_hidden=is_hidden)
    
    # Fetch recent history for context (last 20 messages to keep context window manageable)
    history = database.get_history(limit=20)

    # Strip images from older history messages to prevent token overflow.
    # Base64 images are enormous (can be 1M+ tokens each). Only the most
    # recent image (if any) in the current message should be preserved.
    if len(history) > 1:
        last_msg = history[-1]
        history = llm_client._strip_images(history[:-1]) + [last_msg]

    # Filter out any system-role messages that may have been stored in
    # older database entries (e.g. search/recall results).
    history = [m for m in history if m.get("role") != "system"]

    # Re-read system prompt
    sys_msg = {"role": "system", "content": build_system_prompt()}
    messages = [sys_msg] + history

    def generate():
        response_content = ""
        
        # --- Phase 1: Stream initial response, detect tool use ---
        buffer = ""
        tool_type = None   # 'search' or 'recall' once detected
        tool_query = ""
        
        try:
            for content in llm_client.stream_chat(messages):
                if content:
                    buffer += content
                    
                    # Detect tool tags before streaming content
                    if not tool_type:
                        if "<search>" in buffer:
                            tool_type = "search"
                            yield f"data: {json.dumps({'status': 'searching'})}\n\n"
                        elif "<recall>" in buffer:
                            tool_type = "recall"
                            yield f"data: {json.dumps({'status': 'recalling'})}\n\n"
                    
                    # Once a tool tag is open, wait for it to close
                    if tool_type == "search" and "</search>" in buffer:
                        tool_query = buffer.split("<search>")[1].split("</search>")[0].strip()
                        break
                    elif tool_type == "recall" and "</recall>" in buffer:
                        tool_query = buffer.split("<recall>")[1].split("</recall>")[0].strip()
                        break
                    elif not tool_type:
                        # No tool detected yet — stream content directly
                        response_content += content
                        yield f"data: {json.dumps({'content': content})}\n\n"

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            yield f"data: {json.dumps({'error': f'HTTP Error {e.code}: {error_body}'})}\n\n"
            return
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return
        
        # --- Phase 2: Execute tool and stream second pass ---
        clean_buffer = ""
        tool_results = ""
        
        if tool_query:
            if tool_type == "search":
                tool_results = handle_web_search(tool_query)
                clean_buffer = f"<search>{tool_query}</search>"
            else:
                tool_results = handle_recall(tool_query)
                clean_buffer = f"<recall>{tool_query}</recall>"
        
        if tool_results:
            messages.append({"role": "assistant", "content": clean_buffer})
            messages.append({"role": "system", "content": tool_results})
            pass2_messages = llm_client._strip_images(messages)
            response_content = ""
            
            try:
                for content in llm_client.stream_chat(pass2_messages):
                    if content:
                        response_content += content
                        yield f"data: {json.dumps({'content': content})}\n\n"
            except urllib.error.HTTPError as e:
                error_body = e.read().decode('utf-8')
                yield f"data: {json.dumps({'error': f'HTTP Error {e.code}: {error_body}'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return
        
        # --- Phase 3: Process response tags ---
        response_content = process_response_tags(response_content, history)

        # --- Phase 4: Persist (only the final response, not tool results) ---
        database.add_message('assistant', response_content)
        
        # --- Phase 5: Background memory processing ---
        final_history = history.copy()
        if tool_query:
            final_history.append({"role": "assistant", "content": clean_buffer})
            final_history.append({"role": "system", "content": tool_results})
        final_history.append({"role": "assistant", "content": response_content})
        threading.Thread(
            target=lambda: (
                summarizer.summarize_if_needed(),
                memory_manager.evaluate_memory(final_history)
            ),
            daemon=True
        ).start()

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    import sys
    
    # Allow fallback to browser mode with --browser flag
    if '--browser' in sys.argv:
        app.run(host='localhost', port=5000, debug=True)
    else:
        try:
            import webview
            
            # Load saved window size or use defaults
            window_config_path = os.path.join(DIRECTORY, 'window_state.json')
            default_width, default_height = 1050, 1125
            try:
                if os.path.exists(window_config_path):
                    with open(window_config_path, 'r') as f:
                        wc = json.load(f)
                    default_width = wc.get('width', default_width)
                    default_height = wc.get('height', default_height)
            except Exception:
                pass
            
            # Start Flask in a background thread
            flask_thread = threading.Thread(
                target=lambda: app.run(host='localhost', port=5000, debug=False, use_reloader=False),
                daemon=True
            )
            flask_thread.start()
            
            # Create native window on the main thread
            window = webview.create_window(
                'Confidant',
                'http://localhost:5000',
                width=default_width,
                height=default_height,
                min_size=(380, 500),
            )
            
            def on_closing():
                """Save window size before closing."""
                try:
                    with open(window_config_path, 'w') as f:
                        json.dump({'width': window.width, 'height': window.height}, f)
                except Exception:
                    pass
            
            window.events.closing += on_closing
            
            # persistent storage for localStorage (theme, etc.)
            storage_dir = os.path.join(DIRECTORY, '.webview_storage')
            webview.start(private_mode=False, storage_path=storage_dir)
        except ImportError:
            print("[Confidant] pywebview not installed — falling back to browser mode.")
            print("[Confidant] Install it with: pip install pywebview")
            app.run(host='localhost', port=5000, debug=True)
