# SSH Client Methods Documentation 🔧

## Overview

The `SSHClient` class provides powerful batch execution methods that dramatically simplify robot automation and testing. All methods support timeout control and provide structured results perfect for UIs and monitoring.

## 🎯 **Variable Persistence - Key Finding!**

### ✅ **Variables DO Persist Between Separate Batch Calls**

**CONFIRMED:** Variables, functions, and complex objects (like Opentrons protocols) persist across separate `execute_python_batch()` calls when proper session management is maintained.

```python
# Batch 1: Set variables
client.execute_python_batch([
    ("Set x", "x = 42"),
    ("Set name", "name = 'OpenTrons'"),
])

# Batch 2: Use variables from Batch 1 (SEPARATE call!)
client.execute_python_batch([
    ("Use x", "print(f'x is still: {x}')"),  # ✅ Works!
    ("Use name", "print(f'name: {name}')"),  # ✅ Works!
])

# Batch 3: Complex objects persist too
client.execute_python_batch([
    ("Import execute", "from opentrons import execute"),  # 120s timeout!
    ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
])

# Batch 4: Protocol from Batch 3 is still available
client.execute_python_batch([
    ("Use protocol", "print(f'Protocol: {protocol.api_version}')"),  # ✅ Works!
    ("Home robot", "protocol.home()"),  # ✅ Works!
])
```

### 🔑 **Success Factors**

1. **Stable SSH session** - avoid reconnections that restart Python session
2. **Proper timeouts** - prevent timeouts that cause reconnections
3. **Session management** - maintain Python mode across batches

### ❌ **What Causes Variable Loss**

- SSH connection timeouts → reconnection → new Python session
- Manual disconnection/reconnection
- Network interruptions
- Insufficient timeouts for heavy operations

## 🚀 Core Methods

### 1. `execute_command_batch()`

**Signature:**
```python
execute_command_batch(
    commands: List[Tuple[str, str]], 
    command_delay: float = 0.2,
    show_progress: bool = True,
    stop_on_error: bool = True,
    timeout: Optional[int] = None
) -> List[Dict[str, Any]]
```

**Description:** Core batch execution method that executes commands in the current session mode (Python or shell).

**Parameters:**
- `commands`: List of `(description, command)` tuples
- `command_delay`: Delay between commands in seconds (default: 0.2)
- `show_progress`: Show numbered progress output (default: True)
- `stop_on_error`: Stop execution on first error (default: True)
- `timeout`: Timeout for each command in seconds (uses client default if None)

**Returns:** List of result dictionaries with success/failure info

### 2. `execute_python_batch()`

**Signature:**
```python
execute_python_batch(
    commands: List[Tuple[str, str]], 
    **kwargs
) -> List[Dict[str, Any]]
```

**Description:** Execute batch of Python commands. Automatically switches to Python mode if needed.

**Parameters:**
- `commands`: List of `(description, command)` tuples  
- `**kwargs`: All parameters from `execute_command_batch()` supported:
  - `command_delay`: Delay between commands (default: 0.2)
  - `show_progress`: Show progress output (default: True)
  - `stop_on_error`: Stop on first error (default: True)
  - `timeout`: Timeout per command in seconds

**Perfect for:** Protocol development, state tracking, robot automation

### 3. `execute_shell_batch()`

**Signature:**
```python
execute_shell_batch(
    commands: List[Tuple[str, str]], 
    **kwargs
) -> List[Dict[str, Any]]
```

**Description:** Execute batch of shell commands. Automatically switches to shell mode if needed.

**Parameters:**
- `commands`: List of `(description, command)` tuples
- `**kwargs`: All parameters from `execute_command_batch()` supported

**Perfect for:** System administration, file operations, robot diagnostics

### 4. `send_code_block()`

**Signature:**
```python
send_code_block(
    code: str, 
    description: str = "Code block", 
    timeout: Optional[int] = None
) -> Dict[str, Any]
```

**Description:** Send multi-line Python code (functions, classes, modules) in Python mode.

**Parameters:**
- `code`: Multi-line Python code string
- `description`: Human-readable description for progress output
- `timeout`: Command timeout in seconds (uses client default if None)

**Perfect for:** Loading modules, defining functions, sending complex code

## ⏱️ Timeout Management

### **Critical Timeout Requirements for Opentrons**

```python
# ⚠️ IMPORTANT: Use these timeouts for Opentrons operations

# Client initialization - longer default timeout
client = SSHClient(
    host_alias="ot2_tailscale",
    command_timeout=120,  # 2 minutes default for stability
    password="your_password"
)

# Opentrons import - REQUIRES 120+ seconds
opentrons_import_commands = [
    ("Import execute", "from opentrons import execute"),  # Very slow!
]
results = client.execute_python_batch(
    opentrons_import_commands, 
    timeout=120  # MINIMUM 120 seconds required
)

# Regular protocol operations - 60-90 seconds
protocol_commands = [
    ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
    ("Home robot", "protocol.home()"),
    ("Load labware", "plate = protocol.load_labware(...)"),
]
results = client.execute_python_batch(
    protocol_commands,
    timeout=90  # 90 seconds for hardware operations
)

# Quick commands - 30 seconds or less
quick_commands = [
    ("Check state", "print(protocol.api_version)"),
    ("Simple math", "result = 42 * 2"),
]
results = client.execute_python_batch(
    quick_commands,
    timeout=30  # 30 seconds for simple operations
)
```

### **Timeout Guidelines by Operation**

| Operation Type | Recommended Timeout | Notes |
|---|---|---|
| `from opentrons import execute` | **120+ seconds** | Critical - very slow import |
| Protocol creation | 60-90 seconds | Hardware initialization |
| Robot homing | 60-90 seconds | Physical movement |
| Labware loading | 30-60 seconds | Medium complexity |
| State queries | 10-30 seconds | Fast operations |
| Simple calculations | 5-10 seconds | Very fast |

### Default Timeouts
```python
# Set during client initialization
client = SSHClient(
    host_alias="ot2_tailscale",
    command_timeout=120,  # Recommended for Opentrons
    password="your_password"
)
```

### Per-Command Timeouts
```python
# Override timeout for specific batch
results = client.execute_python_batch(
    commands, 
    timeout=120  # 2 minutes for each command
)

# Override timeout for code block
result = client.send_code_block(
    complex_module_code,
    "Loading complex module",
    timeout=180  # 3 minutes
)
```

### Timeout Scenarios
- **Short operations**: `timeout=10` (state queries, simple calculations)
- **Medium operations**: `timeout=60` (protocol setup, labware loading)
- **Long operations**: `timeout=120` (**Opentrons imports**, complex protocols)
- **Very long operations**: `timeout=300` (full protocol runs, extensive analysis)

## 📊 Return Values

### Batch Methods Return Format
```python
[
    {
        'description': 'Load pipette',
        'command': 'p300 = protocol.load_instrument(...)',
        'success': True,
        'output': '>>> p300 = protocol.load_instrument(...)\n>>> ',
        'error': ''
    },
    {
        'description': 'Invalid command', 
        'command': 'nonexistent_function()',
        'success': False,
        'output': '',
        'error': 'NameError: name "nonexistent_function" is not defined'
    }
]
```

### Code Block Return Format
```python
{
    'success': True,
    'output': '>>> def my_function():\n...     pass\n>>> ',
    'error': ''
}
```

## 🎯 Usage Examples

### **Persistence-Aware Protocol Development**

```python
# Step 1: Import Opentrons (separate batch with long timeout)
import_commands = [
    ("Import execute", "from opentrons import execute"),
]
results1 = client.execute_python_batch(import_commands, timeout=120)

# Step 2: Setup protocol (separate batch, uses import from Step 1)
setup_commands = [
    ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
    ("Home robot", "protocol.home()"),
]
results2 = client.execute_python_batch(setup_commands, timeout=90)

# Step 3: Load labware (separate batch, uses protocol from Step 2)
labware_commands = [
    ("Load tips", "tips = protocol.load_labware('opentrons_96_tiprack_300ul', 1)"),
    ("Load plate", "plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 2)"),
    ("Load pipette", "p300 = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips])"),
]
results3 = client.execute_python_batch(labware_commands, timeout=60)

# Step 4: Run operations (separate batch, uses everything from previous steps)
operation_commands = [
    ("Pick up tip", "p300.pick_up_tip()"),
    ("Aspirate", "p300.aspirate(100, plate['A1'])"),
    ("Dispense", "p300.dispense(100, plate['B1'])"),
    ("Return tip", "p300.return_tip()"),
]
results4 = client.execute_python_batch(operation_commands, timeout=45)

# 🎉 All variables persisted across 4 separate batch calls!
```

### Basic Protocol Setup
```python
setup_commands = [
    ("Import execute", "from opentrons import execute"),
    ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
    ("Home robot", "protocol.home()"),
]

results = client.execute_python_batch(setup_commands, timeout=120)  # Long timeout for import
```

### With Custom Configuration
```python
# Slower execution with longer timeouts
results = client.execute_python_batch(
    commands,
    command_delay=1.0,      # 1 second between commands
    timeout=120,            # 2 minutes per command
    stop_on_error=False,    # Continue even if some fail
    show_progress=True      # Show detailed progress
)
```

### Loading Complex Modules
```python
# Load opentrons_states module
with open('opentrons_states.py', 'r') as f:
    module_code = f.read()

result = client.send_code_block(
    module_code,
    "opentrons_states module", 
    timeout=60
)

if result['success']:
    print("✅ Module loaded successfully")
else:
    print(f"❌ Failed: {result['error']}")
```

## 🔧 Progress Output Format

When `show_progress=True` (default):

```
🐍 Switching to Python mode...
✅ Now in python mode

[ 1/5] Import execute module
         → from opentrons import execute
         ✅ Success

[ 2/5] Create protocol context
         → protocol = execute.get_protocol_api('2.18')
       ✅ API Version 2.18

[ 3/5] Home all axes
       → protocol.home()
       ✅ Homing complete

[ 4/5] Load plate in slot 1
       → plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')
       ✅ Labware loaded

[ 5/5] Load pipette tips
       → tips = protocol.load_labware('opentrons_96_tiprack_300ul', '2')
       ✅ Labware loaded
```

## 🚨 Error Handling

### Timeout Errors
```
[ 3/5] Long running command
       → very_slow_operation()
       ❌ Error: Python command timeout after 30 seconds

❌ Stopping execution due to error in command 3
```

### Python Errors
```
[ 2/5] Invalid syntax
       → invalid python code!
       ❌ Error: Python error in command execution:
SyntaxError: invalid syntax
```

### Connection Errors
```
[ 4/5] Network operation
       → network_call()
       ❌ Error: SSH connection lost

❌ Stopping execution due to error in command 4
```

## 💡 Best Practices

### 1. **Appropriate Timeouts**
```python
# Fast operations
quick_commands = [("Check version", "import sys; print(sys.version)")]
client.execute_python_batch(quick_commands, timeout=10)

# Slow operations  
slow_commands = [("Run full protocol", "protocol.run()")]
client.execute_python_batch(slow_commands, timeout=300)
```

### 2. **Descriptive Names**
```python
# ✅ Good - Clear descriptions
commands = [
    ("Load 96-well plate in slot 1", "plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')"),
    ("Mount P300 on right", "p300 = protocol.load_instrument('p300_single_gen2', 'right')"),
]

# ❌ Less helpful
commands = [
    ("Load labware", "plate = protocol.load_labware(...)"),
    ("Load instrument", "p300 = protocol.load_instrument(...)"),
]
```

### 3. **Error Recovery**
```python
# Continue on non-critical errors
results = client.execute_python_batch(
    commands,
    stop_on_error=False
)

# Process results and retry failed commands
failed_commands = [(r['description'], r['command']) 
                  for r in results if not r['success']]

if failed_commands:
    print(f"Retrying {len(failed_commands)} failed commands...")
    retry_results = client.execute_python_batch(failed_commands)
```

### 4. **Module Loading**
```python
# Load modules before using them
module_result = client.send_code_block(module_code, "Custom module", timeout=60)

if module_result['success']:
    # Now use the module functions
    commands = [
        ("Use module function", "result = my_module_function()"),
        ("Display result", "print(result)"),
    ]
    client.execute_python_batch(commands)
```

## 🎯 Perfect for UI Development

The structured return format makes these methods ideal for web dashboards:

```python
def execute_protocol_api(commands):
    """API endpoint for protocol execution"""
    results = client.execute_python_batch(commands, timeout=120)

return {
        'status': 'success' if all(r['success'] for r in results) else 'partial_failure',
    'total_commands': len(results),
    'successful': sum(1 for r in results if r['success']),
    'failed': sum(1 for r in results if not r['success']),
        'execution_time': sum(r.get('duration', 0) for r in results),
    'results': results
}
```

This enables real-time progress tracking, detailed error reporting, and comprehensive protocol monitoring! 🚀
