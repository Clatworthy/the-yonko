package com.example.demo;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.security.access.prepost.PreAuthorize;

@RestController
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PostMapping("/v1/orders/{id}/confirm")
    @PreAuthorize("hasAuthority('ORDER_CONFIRM')")
    public Order confirm(@PathVariable String id) {
        return orderService.confirm(id);
    }
}
