package com.example.demo;

import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class CreditService {
    private final CreditRepository creditRepository;
    private final DebitPublisher debitPublisher;

    public CreditService(CreditRepository creditRepository, DebitPublisher debitPublisher) {
        this.creditRepository = creditRepository;
        this.debitPublisher = debitPublisher;
    }

    public void debit(String customerId, List<String> ids) {
        CreditResponse response = creditRepository.useCredits(customerId, ids);
        debitPublisher.publish(customerId, response.getRequested());
    }
}
