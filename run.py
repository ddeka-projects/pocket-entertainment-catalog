from pocket_entertainment_catalog.config import load_settings
from pocket_entertainment_catalog.server import run


if __name__ == "__main__":
    run(load_settings())
