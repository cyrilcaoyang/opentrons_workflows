#!/usr/bin/env python3
"""
Launcher script for Opentrons UI
Run this from the project root to start the web dashboard
"""

import sys
import os
import subprocess

def main():
    """Launch the Opentrons UI web dashboard"""
    
    print("🌐 Starting Opentrons UI Dashboard...")
    print("=" * 50)
    
    # Get the path to the UI directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui_path = os.path.join(project_root, 'src', 'opentrons-ui')
    
    # Check if the UI directory exists
    if not os.path.exists(ui_path):
        print("❌ Error: UI directory not found at src/opentrons-ui")
        print("   Make sure you're running this from the project root")
        sys.exit(1)
    
    # Check if web_ui_demo.py exists
    web_ui_file = os.path.join(ui_path, 'web_ui_demo.py')
    if not os.path.exists(web_ui_file):
        print("❌ Error: web_ui_demo.py not found in src/opentrons-ui")
        sys.exit(1)
    
    print(f"📁 UI Path: {ui_path}")
    print(f"🚀 Starting web server...")
    print(f"📊 Dashboard will be available at: http://localhost:8080")
    print(f"🔧 API endpoints available at: http://localhost:8080/api/*")
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 50)
    
    try:
        # Change to UI directory and run the web server
        os.chdir(ui_path)
        subprocess.run([sys.executable, 'web_ui_demo.py'], check=True)
        
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(1)
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 