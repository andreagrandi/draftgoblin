pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    required property string currentSurface
    required property bool compact
    signal surfaceRequested(string surface)

    color: Theme.surfaceLow
    implicitWidth: compact ? 116 : 176

    ButtonGroup {
        id: navigationGroup
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        ColumnLayout {
            Layout.fillWidth: true
            visible: !root.compact
            spacing: 6

            Label {
                Layout.fillWidth: true
                text: "DRAFTGOBLIN"
                color: Theme.primary
                font.pixelSize: 14
                font.bold: true
                font.letterSpacing: 1.2
                horizontalAlignment: Text.AlignHCenter
            }

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 144
                Layout.preferredHeight: 144
                color: Theme.background
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius
                clip: true

                Image {
                    objectName: "draftgoblinLogo"
                    anchors.fill: parent
                    source: "../assets/draftgoblin_logo.png"
                    sourceSize.width: 288
                    sourceSize.height: 288
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                    Accessible.name: "Draftgoblin logo"
                }
            }
        }

        Repeater {
            model: [
                { key: "live", label: "Live Draft", shortLabel: "Draft" },
                { key: "build", label: "Deck Build", shortLabel: "Build" },
                { key: "backtest", label: "Backtest", shortLabel: "Backtest" }
            ]

            delegate: Button {
                id: navigationButton
                required property var modelData

                Layout.fillWidth: true
                Layout.preferredHeight: Theme.targetHeight
                text: root.compact
                    ? navigationButton.modelData.shortLabel
                    : navigationButton.modelData.label
                checkable: true
                checked: root.currentSurface === navigationButton.modelData.key
                ButtonGroup.group: navigationGroup
                Accessible.name: navigationButton.modelData.label
                Accessible.description: "Open the " + navigationButton.modelData.label + " surface."
                onClicked: root.surfaceRequested(navigationButton.modelData.key)

                background: Rectangle {
                    color: navigationButton.checked ? Theme.surfaceHighest : "transparent"
                    border.color: navigationButton.activeFocus
                        ? Theme.focus
                        : navigationButton.checked ? Theme.primary : "transparent"
                    border.width: navigationButton.activeFocus || navigationButton.checked ? 2 : 0
                    radius: Theme.radius
                }

                contentItem: Text {
                    text: navigationButton.text
                    color: navigationButton.checked ? Theme.primary : Theme.textMuted
                    font.bold: navigationButton.checked
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignLeft
                    leftPadding: 10
                }
            }
        }

        Item {
            Layout.fillHeight: true
        }

        Label {
            Layout.fillWidth: true
            text: root.compact ? "Read only" : "Arena integration · Read only"
            color: Theme.textMuted
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.outline
    }
}

