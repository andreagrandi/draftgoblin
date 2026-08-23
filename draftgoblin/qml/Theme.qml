pragma Singleton

import QtQuick 2.15

QtObject {
    readonly property color background: "#131313"
    readonly property color surfaceLow: "#1c1b1b"
    readonly property color surface: "#201f1f"
    readonly property color surfaceHigh: "#2a2a2a"
    readonly property color surfaceHighest: "#353534"
    readonly property color text: "#e5e2e1"
    readonly property color textMuted: "#c4c9b0"
    readonly property color outline: "#444936"
    readonly property color primary: "#aad630"
    readonly property color primaryDark: "#273500"
    readonly property color warning: "#ffb693"
    readonly property color warningDark: "#562000"
    readonly property color error: "#ffb4ab"
    readonly property color errorDark: "#690005"
    readonly property color focus: "#ffa9fa"
    readonly property color whiteMana: "#f9fafb"
    readonly property color blueMana: "#0ea5e9"
    readonly property color blackMana: "#4b5563"
    readonly property color redMana: "#ef4444"
    readonly property color greenMana: "#22c55e"

    readonly property int radius: 4
    readonly property int gutter: 12
    readonly property int panelPadding: 16
    readonly property int targetHeight: 42
    readonly property int narrowBreakpoint: 980

    function colorForMana(symbol) {
        if (symbol === "W") return whiteMana
        if (symbol === "U") return blueMana
        if (symbol === "B") return blackMana
        if (symbol === "R") return redMana
        if (symbol === "G") return greenMana
        return textMuted
    }
}

