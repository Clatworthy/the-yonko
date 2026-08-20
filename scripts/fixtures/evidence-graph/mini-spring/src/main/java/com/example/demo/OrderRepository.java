package com.example.demo;

import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    public Order findById(String id) { return new Order(id); }
    public void save(Order order) {}
}
