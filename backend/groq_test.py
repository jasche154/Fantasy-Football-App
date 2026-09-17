import json
from groq import Groq
from tools.rushleaders import get_rushing_leaders
from tools.schemas import tools
import os
from dotenv import load_dotenv
load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

messages = [{"role": "user", "content": "Where does Bhasyhul Tuten rank in efficiency per attempt in 2025"}]

# Round 1: let the model decide whether/how to call a tool
response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=messages,
    tools=tools,
    tool_choice="auto",
)

response_message = response.choices[0].message
tool_call = response_message.tool_calls[0]  # assume one call for now
args = json.loads(tool_call.function.arguments)

# Round 2: actually run YOUR function with the model's chosen arguments
result = get_rushing_leaders(**args)

# Send the real result back so the model can explain it
messages.append(response_message)
messages.append({
    "role": "tool",
    "tool_call_id": tool_call.id,
    "content": json.dumps(result),
})

final_response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=messages,
    tools = tools,
    tool_choice = "none",
)

print(final_response.choices[0].message.content)