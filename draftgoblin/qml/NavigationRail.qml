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
                { key: "live", label: "Live Draft", shortLabel: "Draft", icon: "gavel" },
                { key: "build", label: "Deck Build", shortLabel: "Build", icon: "style" },
                { key: "backtest", label: "Backtest", shortLabel: "Backtest", icon: "history" }
            ]

            delegate: Button {
                id: navigationButton
                required property var modelData

                readonly property color navigationForeground: !enabled
                    ? Qt.rgba(0.77, 0.79, 0.69, 0.48)
                    : activeFocus
                        ? Theme.focus
                        : checked
                            ? Theme.primary
                            : hovered ? Theme.text : Theme.textMuted

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
                    color: !navigationButton.enabled
                        ? "transparent"
                        : navigationButton.checked
                            ? Theme.surfaceHighest
                            : navigationButton.hovered
                                ? Theme.surfaceHigh : "transparent"
                    border.color: !navigationButton.enabled
                        ? "transparent"
                        : navigationButton.activeFocus
                            ? Theme.focus
                            : navigationButton.checked
                                ? Theme.primary
                                : navigationButton.hovered
                                    ? Theme.outline : "transparent"
                    border.width: !navigationButton.enabled
                        ? 0
                        : navigationButton.activeFocus || navigationButton.checked
                            ? 2
                            : navigationButton.hovered ? 1 : 0
                    radius: Theme.radius
                }

                contentItem: RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: root.compact ? 6 : 10
                    anchors.rightMargin: root.compact ? 6 : 10
                    spacing: root.compact ? 6 : 10

                    Canvas {
                        id: navigationIcon
                        objectName: navigationButton.modelData.key + "NavigationIcon"
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                        Layout.alignment: Qt.AlignVCenter
                        implicitWidth: 24
                        implicitHeight: 24
                        property string iconName: navigationButton.modelData.icon
                        property color iconColor: navigationButton.navigationForeground

                        onIconNameChanged: requestPaint()
                        onIconColorChanged: requestPaint()
                        Component.onCompleted: requestPaint()

                        onPaint: {
                            var context = getContext("2d")
                            context.clearRect(0, 0, width, height)
                            context.strokeStyle = iconColor
                            context.fillStyle = iconColor
                            context.lineWidth = 2
                            context.lineCap = "round"
                            context.lineJoin = "round"

                            if (iconName === "gavel") {
                                context.save()
                                context.translate(12, 11)
                                context.rotate(-Math.PI / 4)
                                context.fillRect(-4, -9, 8, 9)
                                context.fillRect(-1.5, 0, 3, 10)
                                context.restore()
                                context.fillRect(2, 20, 20, 2)
                            } else if (iconName === "style") {
                                context.strokeRect(4, 3, 16, 13)
                                context.strokeRect(2, 7, 16, 13)
                                context.beginPath()
                                context.moveTo(7, 11)
                                context.lineTo(15, 11)
                                context.stroke()
                            } else if (iconName === "history") {
                                context.beginPath()
                                context.arc(12, 12, 8, -Math.PI * 0.78, Math.PI * 1.64)
                                context.stroke()
                                context.beginPath()
                                context.moveTo(4, 4)
                                context.lineTo(4, 9)
                                context.lineTo(9, 7)
                                context.fill()
                                context.beginPath()
                                context.moveTo(12, 7)
                                context.lineTo(12, 12)
                                context.lineTo(16, 14)
                                context.stroke()
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: navigationButton.text
                        color: navigationButton.navigationForeground
                        font.bold: navigationButton.checked
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignLeft
                    }
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

