package com.example.demo;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {
    private final OrderRepository orderRepository;
    private final NotificationClient notificationClient;

    public OrderService(OrderRepository orderRepository, NotificationClient notificationClient) {
        this.orderRepository = orderRepository;
        this.notificationClient = notificationClient;
    }

    @Transactional
    public Order confirm(String orderId) {
        Order order = orderRepository.findById(orderId);
        order.confirm();
        orderRepository.save(order);
        notificationClient.publishConfirmed(orderId);
        return order;
    }
}
