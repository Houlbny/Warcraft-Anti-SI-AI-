"""A deterministic stock exercise; change only this practice copy."""
def remaining(stock: int, ordered: int) -> int:
    return stock + ordered  # BUG: sales should reduce stock


def main() -> None:
    print(f"remaining={remaining(10, 2)}")


if __name__ == "__main__":
    main()
