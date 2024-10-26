from prefect import flow
from functools import wraps

def prefect_wrap_classmethod(cls_instance, method_name):
    """
    Wrap a class method into a Prefect flow, replacing `self` with `cls_instance`.
    
    Args:
        cls_instance: The instance of the class to use as `self`.
        method_name: The name of the method to wrap as a flow.
        
    Returns:
        A wrapped flow function.
    """
    # Get the method from the class
    method = getattr(cls_instance, method_name)
    
    # Unwrap the method if it's decorated
    while hasattr(method, '__wrapped__'):
        method = method.__wrapped__
    
    @wraps(method)
    @flow
    def wrapped_flow(*args, **kwargs):
        return method(*args, **kwargs)
    
    return wrapped_flow


# Examples
from prefect import flow

class Test:
    def __init__(self,num):
        self.x=num

    def add(self, a, b):
        return self.x + a + b

# Create an instance of the class
test_instance = Test(10)

# Wrap the `add` method into a flow, replacing `self` with `test_instance`
add_flow = prefect_wrap_classmethod(test_instance, "add")


if __name__ == "__main__":
    print(add_flow(2, 3))  # Outputs: 6