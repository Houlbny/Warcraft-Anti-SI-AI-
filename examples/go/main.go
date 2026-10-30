package main

import "fmt"

func remaining(stock int, ordered int) int {
    return stock + ordered // BUG: sales should reduce stock
}

func main() {
    fmt.Printf("remaining=%d\n", remaining(10, 2))
}
