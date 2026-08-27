import QtQuick 2.15
import QtQuick.Controls 2.15

Switch {
    id: control

    // Keep the complete switch target large enough for mouse and touch input.
    implicitWidth: 68
    implicitHeight: Theme.targetHeight
    padding: 0
    activeFocusOnTab: true
    focusPolicy: Qt.StrongFocus

    readonly property bool visualChecked: control.checked
    readonly property bool visualUnchecked: !control.visualChecked
    readonly property bool visualDisabled: !control.enabled
    readonly property bool visualFocused: control.activeFocus
    readonly property string visualState: {
        if (control.visualDisabled)
            return "disabled"
        return control.visualChecked ? "checked" : "unchecked"
    }

    // The checked track is intentionally the darkest state. The bright thumb
    // and state glyph keep the on state legible against it.
    readonly property color checkedTrackColor: Theme.background
    readonly property color uncheckedTrackColor: Theme.neutral
    readonly property color disabledTrackColor: Theme.controlDisabled
    readonly property color disabledThumbColor: Theme.controlDisabledContent
    readonly property color visualTrackColor: {
        if (control.visualDisabled)
            return control.disabledTrackColor
        return control.visualChecked ? control.checkedTrackColor : control.uncheckedTrackColor
    }
    readonly property color visualTrackBorderColor: {
        if (control.visualDisabled)
            return Theme.outlineDisabled
        return control.visualChecked ? Theme.primary : Theme.controlNeutralBorder
    }
    readonly property color visualThumbColor: {
        if (control.visualDisabled)
            return control.disabledThumbColor
        return control.visualChecked ? Theme.primary : Theme.controlHighlight
    }
    readonly property color visualThumbBorderColor: {
        if (control.visualDisabled)
            return Theme.outlineDisabled
        return control.visualChecked ? Theme.controlHighlight : Theme.controlNeutralBorder
    }
    readonly property color visualContentColor: {
        if (control.visualDisabled)
            return Theme.controlDisabledContent
        return control.visualChecked ? Theme.controlHighlight : Theme.neutralContent
    }
    readonly property color visualThumbContentColor: {
        if (control.visualDisabled)
            return Theme.controlDisabled
        return control.visualChecked ? Theme.primaryContent : Theme.neutral
    }
    readonly property color visualFocusColor: Theme.focus

    background: Item { }
    contentItem: Item { }

    indicator: Item {
        id: switchIndicator
        objectName: "settingsSwitchIndicator"
        x: (control.width - width) / 2
        y: (control.height - height) / 2
        width: 68
        height: Theme.targetHeight

        Rectangle {
            id: track
            objectName: "settingsSwitchTrack"
            x: (switchIndicator.width - width) / 2
            y: (switchIndicator.height - height) / 2
            width: 58
            height: 28
            radius: height / 2
            color: control.visualTrackColor
            border.color: control.visualTrackBorderColor
            border.width: 1

            Text {
                objectName: "settingsSwitchTrackState"
                anchors.fill: parent
                anchors.leftMargin: control.visualChecked ? 6 : 0
                anchors.rightMargin: control.visualChecked ? 0 : 6
                text: control.visualChecked ? "ON" : "OFF"
                color: control.visualContentColor
                font.pixelSize: 8
                font.bold: true
                horizontalAlignment: control.visualChecked ? Text.AlignLeft : Text.AlignRight
                verticalAlignment: Text.AlignVCenter
                opacity: 0.92
            }
        }

        Rectangle {
            id: thumb
            objectName: "settingsSwitchThumb"
            x: control.visualChecked ? track.x + track.width - width : track.x
            y: track.y
            width: 28
            height: 28
            radius: width / 2
            color: control.visualThumbColor
            border.color: control.visualThumbBorderColor
            border.width: 1

            Text {
                objectName: "settingsSwitchStateText"
                anchors.fill: parent
                text: control.visualChecked ? "ON" : "OFF"
                color: control.visualThumbContentColor
                font.pixelSize: 8
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            Behavior on x {
                NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
            }
        }

        Rectangle {
            objectName: "settingsSwitchFocusRing"
            x: track.x - Theme.controlFocusGap
            y: track.y - Theme.controlFocusGap
            width: track.width + Theme.controlFocusGap * 2
            height: track.height + Theme.controlFocusGap * 2
            radius: height / 2
            color: "transparent"
            border.color: control.visualFocusColor
            border.width: 2
            visible: !control.visualDisabled && control.visualFocused
        }
    }
}
