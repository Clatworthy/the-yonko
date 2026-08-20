package com.example.demo;

public class CreditResponse {
    private final int requested;
    private final int available;

    public CreditResponse(int requested, int available) {
        this.requested = requested;
        this.available = available;
    }

    public int getRequested() {
        return requested;
    }

    public int getAvailable() {
        return available;
    }
}
