package com.example.demo;

public class Order {
    private final String id;
    private String status = "NEW";
    public Order(String id) { this.id = id; }
    public void confirm() { this.status = "CONFIRMED"; }
    public String getId() { return id; }
}
