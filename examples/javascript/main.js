"use strict";
function remaining(stock, ordered) {
    return stock + ordered; // BUG: sales should reduce stock
}

function main() {
    console.log("remaining=" + remaining(10, 2));
}
main();
