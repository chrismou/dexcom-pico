This is a micro python project running on a Raspbery Pi Pico 2 W with a Pimoroni Display Pack 2.8 attached

The software should work as follows:

When starting up the Pico should attempt to connect to the WiFi using the credentials saved in secrets.py. It should show
feedback on the screen to let the user know it's connected, and gracefully handle if it can't connect within a reasonable
amount of time, stopping execution. It should present the user with the option to retry connecting to WiFi by pressing one 
of the buttons on the display.

Once connected, it should start a process of connecting to a remote API every 30 seconds, and display a subset of the data 
on the screen.

The URL of the API is stored in secrets.py.  When connecting you should include an Authorization header with the value 
"Bearer <token>" where <token> is the api_token stored in secrets.py.

Here's an example API response:

```
{
    "id": "ddb0accc-fc0b-5d24-8c19-973560f3b708",
    "system_time": "2025-10-01T20:11:48.000000Z",
    "display_time": "2025-10-01T12:11:48.000000Z",
    "value": 97,
    "unit": "mg/dL",
    "trend": "flat",
    "status": null
}
```

The ID is unique to this time/value, and so can be used as an identifier to check if a new value has been received.

The "system time" is assumed to be UTC-1. The display time is assumed to be UK time (either GMT or BST).

There are three scenarios for the data:

1. The value is set (ie, not null).  if this is the case:
   - Display the value in large text on the left half of the screen. Underneath in small text, display the "unit", and above 
     in small text, use the "system time" to calculate the number of mins since it last updated, and display that number with "mins" beside it.
   - On the right, we want to show a graphic of an arrow depicting the "trend".  The possible values, and what the arrow
     should show, are as follows:
        "doubleUp": 2 arrows pointing upwards
        "singleUp": 1 arrow pointing upwards
        "fortyFiveUp": 1 arrow pointing up at a 45 degree angle
        "flat": 1 arrow pointing right
        "fortyFiveDown": 1 arrow pointing down at a 135 degree angle
        "singleDown": 1 arrow pointing downwards
        "doubleDown": 2 arrows pointing downwards
     - If the trend is anything other than whats listed above, then do not show any arrow
   - The text should be white, unless the value is above 14 or below 4, then it should be red.
2. The value is null and the status is set - if the value is high or low then display that text, along with the trend arrow if 
    it's set
   - If status is any other value, display --- in place of the value
3 The value is null and the status is null, display --- in place of the value

The API should be checked every 30 seconds. If there is a new value, update the display with the new values. If not, leave
it displaying the existing value, but update the mins since it last updated

If any of the 4 buttons are pressed, it should call the API immediately, then decide what to do with it based on the description
above.

If the API cannot be reached, continue to display the existing value as long as the time since the last update is less than 5 mins.
If it's over 5 mins, then update the value to display ---