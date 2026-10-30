#!/bin/sh
# POSIX shell stock exercise. Arguments are fixed integer samples.
remaining() {
    stock=$1
    ordered=$2
    echo "$((stock + ordered))" # BUG: sales should reduce stock
}

printf 'remaining=%s\n' "$(remaining 10 2)"
