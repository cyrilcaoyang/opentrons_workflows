"""
An interactive command-line utility to guide users through Prefect setup.

This script provides a user-friendly menu to display the necessary `prefect`
CLI commands for common setup and configuration tasks. It helps users:
- Create and use a profile for a local Prefect server.
- Log in to Prefect Cloud and select a workspace.
- Switch between different Prefect profiles (`local`, `cloud`, `default`).

The script is purely informational; it prints the commands for the user to
copy and execute in their own terminal, but does not run any commands itself.
This makes it a safe and convenient tool for new and existing users to manage
their Prefect environments without needing to memorize the specific CLI syntax.

To run this utility, execute the script from the command line:
    $ python src/matterlab_opentrons/prefect_setup.py
"""
import textwrap

def main():
    """
    Presents the main interactive menu to the user.
    """
    print("*" * 80)
    print("Opentrons-Prefect Environment Setup Helper")
    print("*" * 80)
    print(textwrap.dedent("""
        This script provides the necessary CLI commands to configure your Prefect
        environment for either local development or cloud-based monitoring.
        
        You currently have the following profiles:
        - `default`: The standard, serverless local tracking. Good for simple scripts.
        - `local`:   A profile for a local Prefect UI server. Great for development.
        - `cloud`:   A profile for Prefect Cloud. Best for production and team collaboration.
        
        Choose an option below to get instructions.
    """))

    while True:
        print("\n--- Main Menu ---")
        print("1. Set up and use a LOCAL Prefect Server")
        print("2. Set up and use Prefect CLOUD")
        print("3. Switch between existing profiles")
        print("4. Exit")

        choice = input("Enter your choice (1-4): ")

        if choice == '1':
            print("\n" + "="*70)
            print("OPTION 1: Set up a LOCAL Prefect Server")
            print("="*70)
            print(textwrap.dedent("""
                A local server gives you the Prefect UI on your own machine.
                
                Step 1: Create a 'local' profile (if you haven't already).
                          This command only needs to be run once.
                ----------------------------------------------------------------------
                prefect profile create local
                prefect profile use local
                ----------------------------------------------------------------------

                Step 2: Start the server. 
                          Open a NEW, SEPARATE terminal for this command, as it needs
                          to keep running in the background.
                ----------------------------------------------------------------------
                prefect server start
                ----------------------------------------------------------------------
                
                Once the server is running, you can access the UI at http://127.0.0.1:4200
                and any `@flow` you run from this project will appear there.
            """))

        elif choice == '2':
            print("\n" + "="*70)
            print("OPTION 2: Set up Prefect CLOUD")
            print("="*70)
            print(textwrap.dedent("""
                Prefect Cloud gives you a hosted, managed platform with workspaces,
                automations, user accounts, and more.
                
                Step 1: Log in to Prefect Cloud.
                          This command will open a web browser for you to authenticate.
                          It will also automatically create and switch you to a 'cloud'
                          profile.
                ----------------------------------------------------------------------
                prefect cloud login
                ----------------------------------------------------------------------
                
                Step 2: Follow the prompts to select a workspace. After logging in,
                          any `@flow` you run will appear in your selected cloud workspace.
            """))

        elif choice == '3':
            print("\n" + "="*70)
            print("OPTION 3: Switch between existing profiles")
            print("="*70)
            print(textwrap.dedent("""
                You can easily switch your active environment.
                
                To use the serverless local environment:
                ----------------------------------------------------------------------
                prefect profile use default
                ----------------------------------------------------------------------
                
                To use the local server (make sure it's running!):
                ----------------------------------------------------------------------
                prefect profile use local
                ----------------------------------------------------------------------
                
                To use Prefect Cloud:
                ----------------------------------------------------------------------
                prefect profile use cloud
                ----------------------------------------------------------------------
            """))

        elif choice == '4':
            print("Exiting.")
            break
        
        else:
            print("Invalid choice. Please enter a number from 1 to 4.")

        input("\\nPress Enter to return to the menu...")

if __name__ == "__main__":
    main() 
