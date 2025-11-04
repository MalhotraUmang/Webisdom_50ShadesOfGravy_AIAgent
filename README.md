# Webisdom_50ShadesOfGravy_AIAgent
A Restaurant Automation Platform enabling customers to Register/Login, View Menu, Book Tables, Order Food, Handle Payments, Cancel Order and Interact with Gemini AI for NLP.

===========================================================
## Fifty Shades of Gravy — Backend API (v1)
## Author: 
## Date: 2025
-----------------------------------------------------------
### Description:
This FastAPI-based backend powers the "Fifty Shades of Gravy" restaurant automation platform, enabling customers to: 
 • Register / Login securely using JWT authentication 
 • View the restaurant menu (fetched dynamically from Google Sheets) 
 • Book tables via chatbot or API routes 
 • Place and manage food orders linked to table bookings 
 • Handle Stripe-based payment checkout flows 
 • Communicate with Gemini AI for natural language order handling 
 • Log and process cancellation requests (to manager via Google Sheets) 
 • Real-time manager-to-customer cancellation notifications integrated into the chatbot experience 
 • Cancellation status polling from Google Sheets 
 • Auto-notification of approval/rejection to users via chatbot 
 • Thread-based periodic sheet monitoring for status updates 
 • Unified session-based notification delivery 
 • Stripe webhook endpoint for payment event handling 

### Key Integrations:
 • Google Apps Script Webhook → live sync of menu, orders, bookings 
 • Stripe Payment API → online checkout and payment validation 
 • Gemini Generative AI → natural-language chatbot processing 
 • FastAPI app with CORS and modular route setup 
 • Background cancellation watcher 
 • Reusable chatbot notification layer 

### Modules Implemented:
 • Authentication utilities (bcrypt + JWT) 
 • Google Sheet I/O (read/write via webhook) 
 • Table booking, order, and cancellation logic 
 • Stripe checkout session management 
 • Chatbot endpoint for interactive customer experience 
 • Pydantic models for request validation 
 • CORS and environment configuration setup 

### Notes:
##### It implements the complete workflow among frontend, backend, and Google Sheet updates, including AI-assisted chatbot booking and order flows with robust asynchronous notification support, enabling smooth coordination between customers and restaurant management within the same chat session.
===========================================================
