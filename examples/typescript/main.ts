function remaining(stock: number, ordered: number): number {
    return stock + ordered; // BUG: sales should reduce stock
}

function main(): void {
    console.log("remaining=" + remaining(10, 2));
}
main();
