public class Main {
    static int remaining(int stock, int ordered) {
        return stock + ordered; // BUG: sales should reduce stock
    }

    public static void main(String[] args) {
        System.out.println("remaining=" + remaining(10, 2));
    }
}
