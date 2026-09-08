"""Test null routing for questions outside the tool registry."""

from edgedash.query.ask import ask

# Three questions clearly outside the registry
test_questions = [
    "What's the weather like today?",  # Weather - no tool for this
    "Tell me a joke",  # Entertainment - no tool for this
    "What's the capital of France?",  # Geography - no tool for this
]

print("Testing null routing for questions outside tool registry:\n")

for i, question in enumerate(test_questions, 1):
    print(f"Test {i}: {question}")
    print("-" * 60)
    
    answer = ask(question)
    
    print(f"Tool used: {answer.tool_used}")
    print(f"Answer text:\n{answer.text}")
    print(f"Rows returned: {len(answer.rows)}")
    print()
    
    # Verify null handling
    if answer.tool_used is None:
        print("✓ PASS: tool_used is None (correct)")
    else:
        print(f"✗ FAIL: tool_used should be None, got {answer.tool_used}")
    
    if "can't answer" in answer.text.lower() or "available tools" in answer.text.lower():
        print("✓ PASS: answer lists available tools")
    else:
        print("✗ FAIL: answer should list available tools")
    
    print("=" * 60)
    print()
