import pandas as pd
from ax.service.ax_client import AxClient, ObjectiveProperties
from sklearn.metrics import mean_absolute_error

# Define the optimize function
def optimize(past_experiments, total=1.0, random_seed=42):
    """
    Takes a list of past experiments and outputs the next R, G, B parameters
    using Bayesian optimization.

    Parameters
    ----------
    past_experiments : list of dict
        Each dictionary in the list contains keys 'R', 'G', 'B', and 'mae'.
    total : float, optional
        The sum constraint for R, G, and B. Defaults to 1.0.
    random_seed : int, optional
        Random seed for reproducibility. Defaults to 42.

    Returns
    -------
    dict
        Next set of optimal parameters for R, G, B.
    """
    # Initialize AxClient
    ax_client = AxClient(random_seed=random_seed)
    
    # Define experiment parameters
    parameters = [
        {"name": "R", "type": "range", "bounds": [0.0, total], "value_type": "float"},
        {"name": "G", "type": "range", "bounds": [0.0, total], "value_type": "float"},
    ]
    
    # Define objective to minimize MAE
    objectives = {"mae": ObjectiveProperties(minimize=True)}
    
    # Create the experiment
    ax_client.create_experiment(
        parameters=parameters, 
        objectives=objectives, 
        parameter_constraints=[f"R + G <= {total}"]
    )
    
    # Load past experiments into AxClient
    for experiment in past_experiments:
        trial_parameters = {"R": experiment["R"], "G": experiment["G"]}
        ax_client.attach_trial(parameters=trial_parameters)
        ax_client.complete_trial(trial_index=len(ax_client.experiment.trials) - 1, 
                                 raw_data={"mae": experiment["mae"]})
    
    # Get the next trial suggestion
    parameterization, _ = ax_client.get_next_trial()
    parameterization["B"] = total - parameterization["R"] - parameterization["G"]
    
    return parameterization

# Simulated case study
if __name__ == "__main__":
    # Define a list of past experiments with simulated results
    past_experiments = [
        {"R": 0.1, "G": 0.6, "B": 0.3, "mae": 0.25},
        {"R": 0.2, "G": 0.5, "B": 0.3, "mae": 0.20},
        {"R": 0.3, "G": 0.4, "B": 0.3, "mae": 0.15},
        {"R": 0.4, "G": 0.3, "B": 0.3, "mae": 0.12},
        {"R": 0.5, "G": 0.2, "B": 0.3, "mae": 0.10},
    ]
    past_experiments = []

    # Call the optimize function with past experiments
    next_parameters = optimize(past_experiments)
    
    # Display the next R, G, B parameters
    print("Next suggested parameters:")
    print(f"R: {next_parameters['R']:.2f}, G: {next_parameters['G']:.2f}, B: {next_parameters['B']:.2f}")

    # Simulate evaluation of the suggested parameters
    target_params = {"R": 0.2, "G": 0.5, "B": 0.3}  # Hypothetical target
    suggested_params = [next_parameters["R"], next_parameters["G"], next_parameters["B"]]
    target_values = list(target_params.values())
    mae = mean_absolute_error(suggested_params, target_values)
    
    print(f"Simulated MAE for the suggested parameters: {mae:.2f}")
